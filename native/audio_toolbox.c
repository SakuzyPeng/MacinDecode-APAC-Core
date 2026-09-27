// SDK types stay on this side of the bridge. Only fixed-width scalars cross the ABI.
#include <AudioToolbox/AudioToolbox.h>
#include <CoreFoundation/CoreFoundation.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

typedef struct {
    AudioFileID file;
    ExtAudioFileRef ext;
    AudioStreamBasicDescription format;
    int fd;
    uint64_t limit;
} ApacFile;

typedef struct {
    int64_t buffer_offset;
    int64_t frame;
    uint32_t bytes;
    uint32_t frames;
    int32_t frame_status;
    int32_t dependency_status;
    int32_t roll_status;
    uint32_t independently_decodable;
    uint32_t preroll_packet_count;
    int64_t roll_distance;
} ApacPacket;

// Only bridge-owned scalar structures cross FFI; SDK structs are built below.
typedef struct { uint32_t label, flags; float coordinates[3]; } ApacLayoutDescription;
typedef struct { uint32_t offset, bytes, frames; } ApacInputPacket;
typedef struct {
    double sample_rate;
    uint32_t fields[7];
    uint32_t has_layout, layout_tag, layout_bitmap, description_count;
    const ApacLayoutDescription *descriptions;
    const uint8_t *cookie;
    uint32_t cookie_bytes;
    uint32_t defer_cookie;
} ApacReplayConfig;
typedef int32_t (*ApacInputProc)(void *, uint32_t, const uint8_t **, uint32_t *, const ApacInputPacket **, uint32_t *);
typedef struct {
    AudioConverterRef converter;
    AudioChannelLayout *layout;
    AudioStreamBasicDescription input, output;
    ApacInputProc input_proc;
    void *context;
    AudioStreamPacketDescription descriptions[64];
} ApacReplay;

static _Thread_local const char *last_operation = "AudioToolbox";
const char *apac_last_operation(void) { return last_operation; }
void apac_free(void *p) { free(p); }

int32_t apac_packet_metadata(ApacFile *h, int64_t packet, ApacPacket *p) {
    if (packet < 0) return kAudio_ParamError;
    memset(p, 0, sizeof(*p));
    AudioFramePacketTranslation frame = {0}; frame.mPacket = packet;
    UInt32 size = sizeof(frame);
    p->frame_status = AudioFileGetProperty(h->file, kAudioFilePropertyPacketToFrame, &size, &frame);
    p->frame = frame.mFrame;
    AudioPacketDependencyInfoTranslation dep = {0}; dep.mPacket = packet; size = sizeof(dep);
    p->dependency_status = AudioFileGetProperty(h->file, kAudioFilePropertyPacketToDependencyInfo, &size, &dep);
    p->independently_decodable = dep.mIsIndependentlyDecodable;
    p->preroll_packet_count = dep.mNumberPrerollPackets;
    AudioPacketRollDistanceTranslation roll = {0}; roll.mPacket = packet; size = sizeof(roll);
    p->roll_status = AudioFileGetProperty(h->file, kAudioFilePropertyPacketToRollDistance, &size, &roll);
    p->roll_distance = roll.mRollDistance;
    return 0;
}

int32_t apac_previous_independent(ApacFile *h, int64_t packet, int64_t *previous) {
    AudioIndependentPacketTranslation info = {0}; info.mPacket = packet;
    UInt32 size = sizeof(info);
    last_operation = "AudioFileGetProperty(PreviousIndependentPacket)";
    OSStatus s = AudioFileGetProperty(h->file, kAudioFilePropertyPreviousIndependentPacket, &size, &info);
    if (!s) *previous = info.mIndependentlyDecodablePacket;
    return s;
}

int32_t apac_close(ApacFile *h) {
    if (!h) return 0;
    OSStatus s = 0;
    if (h->ext) s = ExtAudioFileDispose(h->ext);
    if (h->file) { OSStatus t = AudioFileClose(h->file); if (!s) s = t; }
    if (h->fd >= 0 && close(h->fd) != 0 && !s) s = kAudioFileUnspecifiedError;
    free(h);
    return s;
}

int32_t apac_open(const uint8_t *path, uint32_t length, ApacFile **out) {
    *out = NULL;
    last_operation = "CFURLCreateFromFileSystemRepresentation";
    CFURLRef url = CFURLCreateFromFileSystemRepresentation(NULL, path, length, false);
    if (!url) return kAudio_MemFullError;
    ApacFile *h = calloc(1, sizeof(*h));
    if (!h) { CFRelease(url); return kAudio_MemFullError; }
    h->fd = -1;
    last_operation = "AudioFileOpenURL";
    OSStatus s = AudioFileOpenURL(url, kAudioFileReadPermission, 0, &h->file);
    CFRelease(url);
    if (!s) {
        UInt32 n = sizeof(h->format);
        last_operation = "AudioFileGetProperty(DataFormat)";
        s = AudioFileGetProperty(h->file, kAudioFilePropertyDataFormat, &n, &h->format);
    }
    if (s) { apac_close(h); return s; }
    *out = h;
    return 0;
}

int32_t apac_format(ApacFile *h, double *rate, uint32_t *fields) {
    *rate = h->format.mSampleRate;
    fields[0] = h->format.mFormatID;
    fields[1] = h->format.mFormatFlags;
    fields[2] = h->format.mBytesPerPacket;
    fields[3] = h->format.mFramesPerPacket;
    fields[4] = h->format.mBytesPerFrame;
    fields[5] = h->format.mChannelsPerFrame;
    fields[6] = h->format.mBitsPerChannel;
    return 0;
}

int32_t apac_property(ApacFile *h, uint32_t prop, uint8_t **out, uint32_t *length) {
    *out = NULL; *length = 0;
    last_operation = "AudioFileGetPropertyInfo";
    UInt32 n = 0;
    OSStatus s = AudioFileGetPropertyInfo(h->file, prop, &n, NULL);
    if (s) return s;
    // A malformed property must not cause an unbounded allocation.
    if (n > 8 * 1024 * 1024) return kAudioFileInvalidFileError;
    uint8_t *p = calloc(n ? n : 1, 1);
    if (!p) return kAudio_MemFullError;
    last_operation = "AudioFileGetProperty";
    UInt32 capacity = n;
    s = AudioFileGetProperty(h->file, prop, &n, p);
    if (s || n > capacity) { free(p); return s ? s : kAudioFileInvalidFileError; }
    *out = p; *length = n;
    return 0;
}

int32_t apac_layout_name(const uint8_t *data, uint32_t length, char *out, uint32_t capacity) {
    last_operation = "AudioFormatGetProperty(ChannelLayoutName)";
    CFStringRef name = NULL;
    UInt32 n = sizeof(name);
    OSStatus s = AudioFormatGetProperty(kAudioFormatProperty_ChannelLayoutName, length, data, &n, &name);
    if (s) return s;
    Boolean ok = name && CFStringGetCString(name, out, capacity, kCFStringEncodingUTF8);
    if (name) CFRelease(name);
    return ok ? 0 : kAudioFileInvalidFileError;
}

int32_t apac_read_packets(ApacFile *h, int64_t start, uint32_t wanted,
                         uint8_t *buffer, uint32_t capacity, ApacPacket *out,
                         uint32_t *count, uint32_t *bytes, uint32_t *eof) {
    *count = 0; *bytes = 0; *eof = 0;
    if (start < 0 || wanted > 64 || wanted == 0) return kAudio_ParamError;
    AudioStreamPacketDescription desc[64] = {0};
    UInt32 num = wanted, n = capacity;
    last_operation = "AudioFileReadPacketData";
    OSStatus s = AudioFileReadPacketData(h->file, false, &n, desc, start, &num, buffer);
    if (s == kAudioFileEndOfFileError) { *eof = 1; s = 0; }
    if (s) return s;
    if (num > wanted || n > capacity) return kAudioFileInvalidFileError;
    for (UInt32 i = 0; i < num; ++i) {
        ApacPacket *p = &out[i];
        memset(p, 0, sizeof(*p));
        p->buffer_offset = h->format.mBytesPerPacket ? (int64_t)i * h->format.mBytesPerPacket : desc[i].mStartOffset;
        p->bytes = h->format.mBytesPerPacket ? h->format.mBytesPerPacket : desc[i].mDataByteSize;
        p->frames = desc[i].mVariableFramesInPacket ? desc[i].mVariableFramesInPacket : h->format.mFramesPerPacket;
        if (p->buffer_offset < 0 || (uint64_t)p->buffer_offset + p->bytes > n) return kAudioFileInvalidFileError;
        AudioFramePacketTranslation frame = {0}; frame.mPacket = start + i;
        UInt32 size = sizeof(frame);
        p->frame_status = AudioFileGetProperty(h->file, kAudioFilePropertyPacketToFrame, &size, &frame);
        p->frame = frame.mFrame;
        AudioPacketDependencyInfoTranslation dep = {0}; dep.mPacket = start + i;
        size = sizeof(dep);
        p->dependency_status = AudioFileGetProperty(h->file, kAudioFilePropertyPacketToDependencyInfo, &size, &dep);
        p->independently_decodable = dep.mIsIndependentlyDecodable;
        p->preroll_packet_count = dep.mNumberPrerollPackets;
        AudioPacketRollDistanceTranslation roll = {0}; roll.mPacket = start + i;
        size = sizeof(roll);
        p->roll_status = AudioFileGetProperty(h->file, kAudioFilePropertyPacketToRollDistance, &size, &roll);
        p->roll_distance = roll.mRollDistance;
    }
    *count = num; *bytes = n;
    return 0;
}

static AudioStreamBasicDescription pcm_format(double rate, uint32_t channels) {
    AudioStreamBasicDescription pcm = {0};
    pcm.mSampleRate = rate; pcm.mFormatID = kAudioFormatLinearPCM;
    pcm.mFormatFlags = kAudioFormatFlagIsFloat | kAudioFormatFlagIsPacked | kAudioFormatFlagsNativeEndian;
    pcm.mBytesPerPacket = pcm.mBytesPerFrame = channels * sizeof(float);
    pcm.mFramesPerPacket = 1; pcm.mChannelsPerFrame = channels; pcm.mBitsPerChannel = 32;
    return pcm;
}

int32_t apac_replay_close(ApacReplay *h) {
    if (!h) return 0;
    OSStatus s = h->converter ? AudioConverterDispose(h->converter) : 0;
    free(h->layout); free(h);
    return s;
}

static OSStatus replay_input(AudioConverterRef converter, UInt32 *packets, AudioBufferList *data,
                             AudioStreamPacketDescription **descriptions, void *context) {
    (void)converter;
    ApacReplay *h = context;
    const uint8_t *bytes = NULL; const ApacInputPacket *input = NULL;
    uint32_t size = 0, count = 0, wanted = *packets < 64 ? *packets : 64;
    if (!wanted) return kAudio_ParamError;
    OSStatus s = h->input_proc(h->context, wanted, &bytes, &size, &input, &count);
    if (s) { *packets = 0; return s; }
    if (count > wanted || size > 16 * 1024 * 1024 || (count && (!bytes || !input))) return kAudio_ParamError;
    uint64_t end = 0;
    for (uint32_t i = 0; i < count; ++i) {
        if (input[i].offset != end || !input[i].bytes || !input[i].frames) return kAudio_ParamError;
        end += input[i].bytes; if (end > size) return kAudio_ParamError;
        h->descriptions[i].mStartOffset = input[i].offset;
        h->descriptions[i].mDataByteSize = input[i].bytes;
        h->descriptions[i].mVariableFramesInPacket = h->input.mFramesPerPacket ? 0 : input[i].frames;
    }
    if (end != size) return kAudio_ParamError;
    *packets = count;
    data->mNumberBuffers = 1;
    data->mBuffers[0].mNumberChannels = h->input.mChannelsPerFrame;
    data->mBuffers[0].mData = (void *)bytes;
    data->mBuffers[0].mDataByteSize = size;
    if (descriptions) *descriptions = count ? h->descriptions : NULL;
    return 0;
}

int32_t apac_replay_create(const ApacReplayConfig *config, ApacInputProc input_proc, void *context, ApacReplay **out) {
    *out = NULL;
    if (!config || !input_proc || !config->fields[5] || config->fields[5] > 1024
        || config->description_count > 1024 || config->sample_rate <= 0
        || config->fields[0] != kAudioFormatAPAC) return kAudio_ParamError;
    ApacReplay *h = calloc(1, sizeof(*h));
    if (!h) return kAudio_MemFullError;
    h->input_proc = input_proc; h->context = context;
    h->input.mSampleRate = config->sample_rate;
    h->input.mFormatID = config->fields[0]; h->input.mFormatFlags = config->fields[1];
    h->input.mBytesPerPacket = config->fields[2]; h->input.mFramesPerPacket = config->fields[3];
    h->input.mBytesPerFrame = config->fields[4]; h->input.mChannelsPerFrame = config->fields[5];
    h->input.mBitsPerChannel = config->fields[6];
    h->output = pcm_format(config->sample_rate, config->fields[5]);
    last_operation = "AudioConverterNew(APAC replay)";
    OSStatus s = AudioConverterNew(&h->input, &h->output, &h->converter);
    if (s) goto fail;
    if (!config->defer_cookie) {
        last_operation = "AudioConverterSetProperty(DecompressionMagicCookie)";
        s = AudioConverterSetProperty(h->converter, kAudioConverterDecompressionMagicCookie, config->cookie_bytes, config->cookie);
        if (s) goto fail;
    }
    if (config->has_layout) {
        UInt32 size = offsetof(AudioChannelLayout, mChannelDescriptions) + config->description_count * sizeof(AudioChannelDescription);
        h->layout = calloc(1, size);
        if (!h->layout) { s = kAudio_MemFullError; goto fail; }
        h->layout->mChannelLayoutTag = config->layout_tag;
        h->layout->mChannelBitmap = config->layout_bitmap;
        h->layout->mNumberChannelDescriptions = config->description_count;
        for (uint32_t i = 0; i < config->description_count; ++i) {
            AudioChannelDescription *d = &h->layout->mChannelDescriptions[i];
            d->mChannelLabel = config->descriptions[i].label;
            d->mChannelFlags = config->descriptions[i].flags;
            memcpy(d->mCoordinates, config->descriptions[i].coordinates, sizeof(d->mCoordinates));
        }
        last_operation = "AudioConverterSetProperty(InputChannelLayout/replay)";
        s = AudioConverterSetProperty(h->converter, kAudioConverterInputChannelLayout, size, h->layout);
        if (s) goto fail;
        last_operation = "AudioConverterSetProperty(OutputChannelLayout/replay)";
        s = AudioConverterSetProperty(h->converter, kAudioConverterOutputChannelLayout, size, h->layout);
        if (s) goto fail;
    }
    AudioStreamBasicDescription actual = {0}; UInt32 size = sizeof(actual);
    last_operation = "AudioConverterGetProperty(CurrentInputStreamDescription/replay)";
    s = AudioConverterGetProperty(h->converter, kAudioConverterCurrentInputStreamDescription, &size, &actual);
    if (s) goto fail;
    if (actual.mSampleRate != h->input.mSampleRate || actual.mChannelsPerFrame != h->input.mChannelsPerFrame
        || actual.mFormatID != h->input.mFormatID) { s = kAudioConverterErr_FormatNotSupported; goto fail; }
    size = sizeof(actual);
    last_operation = "AudioConverterGetProperty(CurrentOutputStreamDescription/replay)";
    s = AudioConverterGetProperty(h->converter, kAudioConverterCurrentOutputStreamDescription, &size, &actual);
    if (s) goto fail;
    if (actual.mSampleRate != h->output.mSampleRate || actual.mChannelsPerFrame != h->output.mChannelsPerFrame
        || actual.mFormatID != kAudioFormatLinearPCM || actual.mFormatFlags != h->output.mFormatFlags
        || actual.mBitsPerChannel != 32 || actual.mBytesPerFrame != h->output.mBytesPerFrame
        || actual.mFramesPerPacket != 1) { s = kAudioConverterErr_FormatNotSupported; goto fail; }
    *out = h; return 0;
fail:
    apac_replay_close(h); return s;
}

int32_t apac_replay_read(ApacReplay *h, float *buffer, uint32_t *frames) {
    if (!h || !*frames || *frames > 8192) return kAudio_ParamError;
    AudioBufferList data = {0}; data.mNumberBuffers = 1;
    data.mBuffers[0].mData = buffer; data.mBuffers[0].mNumberChannels = h->output.mChannelsPerFrame;
    data.mBuffers[0].mDataByteSize = *frames * h->output.mBytesPerFrame;
    last_operation = "AudioConverterFillComplexBuffer(replay)";
    return AudioConverterFillComplexBuffer(h->converter, replay_input, h, frames, &data, NULL);
}

int32_t apac_replay_property(ApacReplay *h, uint32_t property, uint32_t *words, uint32_t count) {
    UInt32 size = count * sizeof(uint32_t);
    last_operation = "AudioConverterGetProperty(replay)";
    OSStatus s = AudioConverterGetProperty(h->converter, property, &size, words);
    return s ? s : (size == count * sizeof(uint32_t) ? 0 : kAudioConverterErr_BadPropertySizeError);
}

int32_t apac_replay_set_cookie(ApacReplay *h, const uint8_t *cookie, uint32_t bytes) {
    last_operation = "AudioConverterSetProperty(DecompressionMagicCookie/replay)";
    return AudioConverterSetProperty(h->converter, kAudioConverterDecompressionMagicCookie, bytes, cookie);
}

int32_t apac_replay_set_off_property(ApacReplay *h, uint32_t property) {
    if (!h || !h->converter) return kAudio_ParamError;
    switch (property) {
        case kAudioCodecPropertyDynamicRangeControlMode:
        case kAudioCodecPropertyAdjustCompressionProfile:
        case kAudioCodecPropertyAdjustTargetLevelConstant:
            break;
        default: return kAudio_ParamError;
    }
    UInt32 value = 0;
    last_operation = "AudioConverterSetProperty(replay processing off)";
    return AudioConverterSetProperty(h->converter, property, sizeof(value), &value);
}

int32_t apac_prepare_decode(ApacFile *h, int64_t start, int64_t *length) {
    if (h->ext || start < 0 || h->format.mChannelsPerFrame == 0 || h->format.mChannelsPerFrame > 1024) return kAudio_ParamError;
    last_operation = "ExtAudioFileWrapAudioFileID(read)";
    OSStatus s = ExtAudioFileWrapAudioFileID(h->file, false, &h->ext);
    if (s) return s;
    AudioStreamBasicDescription pcm = pcm_format(h->format.mSampleRate, h->format.mChannelsPerFrame);
    last_operation = "ExtAudioFileSetProperty(ClientDataFormat)";
    s = ExtAudioFileSetProperty(h->ext, kExtAudioFileProperty_ClientDataFormat, sizeof(pcm), &pcm);
    if (s) return s;
    uint8_t *layout = NULL; uint32_t layout_length = 0;
    s = apac_property(h, kAudioFilePropertyChannelLayout, &layout, &layout_length);
    if (!s && layout_length) {
        last_operation = "ExtAudioFileSetProperty(ClientChannelLayout)";
        s = ExtAudioFileSetProperty(h->ext, kExtAudioFileProperty_ClientChannelLayout, layout_length, layout);
        free(layout);
        if (s) return s;
    } else { free(layout); /* Missing layout is recorded by the Rust metadata reader. */ }
    UInt32 size = sizeof(*length);
    last_operation = "ExtAudioFileGetProperty(FileLengthFrames)";
    s = ExtAudioFileGetProperty(h->ext, kExtAudioFileProperty_FileLengthFrames, &size, length);
    if (s) return s;
    if (start > *length) { last_operation = "ExtAudioFileSeek(range)"; return kExtAudioFileError_InvalidSeek; }
    last_operation = "ExtAudioFileSeek";
    return ExtAudioFileSeek(h->ext, start);
}

int32_t apac_read_pcm(ApacFile *h, float *buffer, uint32_t *frames) {
    last_operation = "ExtAudioFileRead";
    if (!h->ext || (uint64_t)*frames * h->format.mChannelsPerFrame * 4 > UINT32_MAX) return kAudio_ParamError;
    AudioBufferList list = {0}; list.mNumberBuffers = 1;
    list.mBuffers[0].mNumberChannels = h->format.mChannelsPerFrame;
    list.mBuffers[0].mDataByteSize = *frames * h->format.mChannelsPerFrame * 4;
    list.mBuffers[0].mData = buffer;
    return ExtAudioFileRead(h->ext, frames, &list);
}

int32_t apac_converter_property(ApacFile *h, uint32_t prop, uint32_t *value) {
    if (!h->ext) return kAudio_ParamError;
    AudioConverterRef converter = NULL;
    UInt32 size = sizeof(converter);
    last_operation = "ExtAudioFileGetProperty(AudioConverter)";
    OSStatus s = ExtAudioFileGetProperty(h->ext, kExtAudioFileProperty_AudioConverter, &size, &converter);
    if (s) return s;
    if (!converter) return kAudioConverterErr_PropertyNotSupported;
    last_operation = "AudioConverterGetProperty";
    size = sizeof(*value);
    return AudioConverterGetProperty(converter, prop, &size, value);
}

// Bounded callback I/O keeps encoder writes (including final headers) below the quota.
static OSStatus read_cb(void *ctx, SInt64 pos, UInt32 wanted, void *out, UInt32 *actual) {
    ApacFile *h = ctx; *actual = 0;
    if (pos < 0) return kAudio_ParamError;
    ssize_t n;
    do { n = pread(h->fd, out, wanted, pos); } while (n < 0 && errno == EINTR);
    if (n < 0) return kAudioFileUnspecifiedError;
    *actual = (UInt32)n; return 0;
}
static OSStatus write_cb(void *ctx, SInt64 pos, UInt32 wanted, const void *data, UInt32 *actual) {
    ApacFile *h = ctx; *actual = 0;
    if (pos < 0 || (uint64_t)pos > h->limit || wanted > h->limit - (uint64_t)pos) return 'limt';
    while (*actual < wanted) {
        ssize_t n = pwrite(h->fd, (const uint8_t *)data + *actual, wanted - *actual, pos + *actual);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) return kAudioFileUnspecifiedError;
        *actual += (UInt32)n;
    }
    return 0;
}
static SInt64 size_cb(void *ctx) {
    struct stat st;
    return fstat(((ApacFile *)ctx)->fd, &st) == 0 ? st.st_size : 0;
}
static OSStatus resize_cb(void *ctx, SInt64 size) {
    ApacFile *h = ctx;
    if (size < 0 || (uint64_t)size > h->limit) return 'limt';
    return ftruncate(h->fd, size) == 0 ? 0 : kAudioFileUnspecifiedError;
}

int32_t apac_create_encoder(const char *path, double rate, uint32_t channels, uint32_t layout_tag,
                            uint32_t bitrate, uint32_t quality, uint32_t drc_configuration,
                            uint64_t limit, ApacFile **out) {
    *out = NULL;
    if (!channels || channels > 1024 || rate <= 0) return kAudio_ParamError;
    ApacFile *h = calloc(1, sizeof(*h));
    if (!h) return kAudio_MemFullError;
    h->fd = -1; h->limit = limit;
    last_operation = "open(APAC output, O_EXCL)";
    h->fd = open(path, O_CREAT | O_EXCL | O_RDWR, 0600);
    if (h->fd < 0) { free(h); return kAudioFilePermissionsError; }
    h->format.mSampleRate = rate;
    h->format.mFormatID = kAudioFormatAPAC;
    h->format.mChannelsPerFrame = channels;
    UInt32 size = sizeof(h->format);
    last_operation = "AudioFormatGetProperty(FormatInfo/APAC)";
    OSStatus s = AudioFormatGetProperty(kAudioFormatProperty_FormatInfo, 0, NULL, &size, &h->format);
    if (s) goto fail;
    last_operation = "AudioFileInitializeWithCallbacks(CAF/APAC)";
    s = AudioFileInitializeWithCallbacks(h, read_cb, write_cb, size_cb, resize_cb,
                                        kAudioFileCAFType, &h->format, 0, &h->file);
    if (s) goto fail;
    AudioChannelLayout layout = {0}; layout.mChannelLayoutTag = layout_tag;
    UInt32 layout_size = offsetof(AudioChannelLayout, mChannelDescriptions);
    last_operation = "AudioFileSetProperty(ChannelLayout)";
    s = AudioFileSetProperty(h->file, kAudioFilePropertyChannelLayout, layout_size, &layout);
    if (s) goto fail;
    last_operation = "ExtAudioFileWrapAudioFileID(write)";
    s = ExtAudioFileWrapAudioFileID(h->file, true, &h->ext);
    if (s) goto fail;
    AudioStreamBasicDescription pcm = pcm_format(rate, channels);
    last_operation = "ExtAudioFileSetProperty(ClientDataFormat/encode)";
    s = ExtAudioFileSetProperty(h->ext, kExtAudioFileProperty_ClientDataFormat, sizeof(pcm), &pcm);
    if (s) goto fail;
    last_operation = "ExtAudioFileSetProperty(ClientChannelLayout/encode)";
    s = ExtAudioFileSetProperty(h->ext, kExtAudioFileProperty_ClientChannelLayout, layout_size, &layout);
    if (s) goto fail;
    AudioConverterRef converter = NULL; size = sizeof(converter);
    last_operation = "ExtAudioFileGetProperty(AudioConverter/encode)";
    s = ExtAudioFileGetProperty(h->ext, kExtAudioFileProperty_AudioConverter, &size, &converter);
    if (s) goto fail;
    if (bitrate) {
        last_operation = "AudioConverterSetProperty(EncodeBitRate)";
        s = AudioConverterSetProperty(converter, kAudioConverterEncodeBitRate, sizeof(bitrate), &bitrate);
        if (s) goto fail;
    }
    if (quality != UINT32_MAX) {
        last_operation = "AudioConverterSetProperty(CodecQuality)";
        s = AudioConverterSetProperty(converter, kAudioConverterCodecQuality, sizeof(quality), &quality);
        if (s) goto fail;
    }
    if (drc_configuration != UINT32_MAX) {
        UInt32 setting;
        last_operation = "DRC configuration selector";
        switch (drc_configuration) {
            case 0: setting = kAudioCodecDynamicRangeControlConfiguration_None; break;
            case 1: setting = kAudioCodecDynamicRangeControlConfiguration_Music; break;
            case 2: setting = kAudioCodecDynamicRangeControlConfiguration_Speech; break;
            case 3: setting = kAudioCodecDynamicRangeControlConfiguration_Movie; break;
            case 4: setting = kAudioCodecDynamicRangeControlConfiguration_Capture; break;
            default: s = kAudio_ParamError; goto fail;
        }
        last_operation = "AudioConverterSetProperty(DynamicRangeControlConfiguration/cdrc)";
        s = AudioConverterSetProperty(converter, kAudioCodecPropertyDynamicRangeControlConfiguration,
                                      sizeof(setting), &setting);
        if (s) goto fail;
    }
    if (bitrate || quality != UINT32_MAX || drc_configuration != UINT32_MAX) {
        CFArrayRef config = NULL;
        last_operation = "ExtAudioFileSetProperty(ConverterConfig)";
        s = ExtAudioFileSetProperty(h->ext, kExtAudioFileProperty_ConverterConfig, sizeof(config), &config);
        if (s) goto fail;
    }
    *out = h; return 0;
fail:
    apac_close(h); return s;
}

int32_t apac_write_pcm(ApacFile *h, const float *buffer, uint32_t frames) {
    last_operation = "ExtAudioFileWrite";
    if ((uint64_t)frames * h->format.mChannelsPerFrame * 4 > UINT32_MAX) return kAudio_ParamError;
    AudioBufferList list = {0}; list.mNumberBuffers = 1;
    list.mBuffers[0].mNumberChannels = h->format.mChannelsPerFrame;
    list.mBuffers[0].mDataByteSize = frames * h->format.mChannelsPerFrame * 4;
    list.mBuffers[0].mData = (void *)buffer;
    return ExtAudioFileWrite(h->ext, frames, &list);
}
