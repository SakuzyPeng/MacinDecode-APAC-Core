// Diagnostic interposition of public Accelerate entry points in our probe process.
#include <Accelerate/Accelerate.h>
#include <CommonCrypto/CommonDigest.h>
#include <dlfcn.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct { vDSP_DFT_Setup setup; vDSP_Length n; int direction; } Entry;
static Entry entries[128];
static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static int output_offset = -1;
static uint32_t tag;
static uint64_t calls, unknown;
static FILE *logfile;
static const char *logpath;
static vDSP_DFT_Setup (*create_real)(vDSP_DFT_Setup, vDSP_Length, vDSP_DFT_Direction);
static void (*execute_real)(const struct vDSP_DFT_SetupStruct *, const float *, const float *, float *, float *);
static void (*destroy_real)(vDSP_DFT_Setup);

__attribute__((constructor)) static void init_trace(void) {
    // dyld leaves references from the interposing image bound to the originals.
    create_real = vDSP_DFT_zop_CreateSetup;
    execute_real = vDSP_DFT_Execute;
    destroy_real = vDSP_DFT_DestroySetup;
    if (!create_real || !execute_real || !destroy_real) abort();
    logpath = getenv("APAC_DFT_TRACE_FILE");
    const char *mode = getenv("APAC_DFT_OUTPUT_OFFSET");
    if (mode) { output_offset = atoi(mode); if (output_offset != 0 && output_offset != 16) abort(); }
}
void trace_control(int offset, uint32_t label) {
    if (offset != -1 && offset != 0 && offset != 16 && offset != 32 && offset != 48) abort();
    output_offset = offset;
    tag = label;
}
uint64_t trace_call_count(void) { return calls; }
uint64_t trace_unknown_count(void) { return unknown; }

static vDSP_DFT_Setup wrapped_create(vDSP_DFT_Setup previous, vDSP_Length n, vDSP_DFT_Direction direction) {
    vDSP_DFT_Setup result = create_real(previous, n, direction);
    if (result) {
        pthread_mutex_lock(&lock);
        unsigned i;
        for (i = 0; i < 128; ++i) if (!entries[i].setup || entries[i].setup == result) break;
        if (i == 128) abort();
        entries[i] = (Entry){result, n, direction};
        pthread_mutex_unlock(&lock);
    }
    return result;
}
static void wrapped_destroy(vDSP_DFT_Setup setup) {
    pthread_mutex_lock(&lock);
    for (unsigned i = 0; i < 128; ++i) if (entries[i].setup == setup) entries[i].setup = NULL;
    pthread_mutex_unlock(&lock);
    destroy_real(setup);
}
static void hash_pair(const float *a, const float *b, vDSP_Length n, char out[65]) {
    unsigned char digest[32];
    CC_SHA256_CTX context;
    CC_SHA256_Init(&context);
    CC_SHA256_Update(&context, a, (CC_LONG)(n*4));
    CC_SHA256_Update(&context, b, (CC_LONG)(n*4));
    CC_SHA256_Final(digest, &context);
    for (unsigned i = 0; i < 32; ++i) sprintf(out+2*i, "%02x", digest[i]);
}
static void wrapped_execute(const struct vDSP_DFT_SetupStruct *setup,
                const float *ir, const float *ii, float *or_, float *oi) {
    Entry entry = {0};
    pthread_mutex_lock(&lock);
    if (logpath && !logfile) {
        logfile = fopen(logpath, "wx");
        if (!logfile) { perror("DFT trace destination"); abort(); }
    }
    for (unsigned i = 0; i < 128; ++i) if (entries[i].setup == setup) { entry = entries[i]; break; }
    uint64_t serial = ++calls;
    pthread_mutex_unlock(&lock);
    if (!entry.setup || entry.n > 65536) {
        ++unknown;
        execute_real(setup, ir, ii, or_, oi);
        return;
    }
    char before[65] = {0}, after[65] = {0};
    if (logfile) hash_pair(ir, ii, entry.n, before);
    float *actual_r = or_, *actual_i = oi;
    void *scratch = NULL;
    int mode = output_offset;
    if (mode >= 0) {
        size_t stride = (entry.n*4+63) & ~(size_t)63;
        if (posix_memalign(&scratch, 64, 2*stride+64)) abort();
        actual_r = (float *)((char *)scratch+mode);
        actual_i = (float *)((char *)scratch+stride+mode);
    }
    execute_real(setup, ir, ii, actual_r, actual_i);
    if (logfile) {
        hash_pair(actual_r, actual_i, entry.n, after);
        fprintf(logfile, "{\"call\":%llu,\"tag\":%u,\"length\":%lu,\"direction\":%d,"
            "\"ir_mod64\":%lu,\"ii_mod64\":%lu,\"or_mod64\":%lu,\"oi_mod64\":%lu,"
            "\"actual_or_mod64\":%lu,\"actual_oi_mod64\":%lu,\"mode\":%d,"
            "\"input_sha256\":\"%s\",\"output_sha256\":\"%s\"}\n",
            serial, tag, entry.n, entry.direction, (uintptr_t)ir%64, (uintptr_t)ii%64,
            (uintptr_t)or_%64, (uintptr_t)oi%64, (uintptr_t)actual_r%64, (uintptr_t)actual_i%64,
            mode, before, after);
        fflush(logfile);
    }
    if (scratch) {
        memcpy(or_, actual_r, entry.n*4);
        memcpy(oi, actual_i, entry.n*4);
        free(scratch);
    }
}

__attribute__((destructor)) static void finish_trace(void) {
    if (logfile) {
        fprintf(logfile, "{\"audit\":true,\"calls\":%llu,\"unknown\":%llu}\n", calls, unknown);
        fclose(logfile);
    }
}

#define INTERPOSE(replacement, original) \
__attribute__((used,section("__DATA,__interpose"))) \
static const struct { const void *replace; const void *original; } pair_##original = { (void *)&replacement, (void *)&original };
INTERPOSE(wrapped_create, vDSP_DFT_zop_CreateSetup)
INTERPOSE(wrapped_execute, vDSP_DFT_Execute)
INTERPOSE(wrapped_destroy, vDSP_DFT_DestroySetup)
