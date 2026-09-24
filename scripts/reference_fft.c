// Diagnostic reference only: either use a Float64 DIF DFT with one Float32
// output rounding, or observe the original vDSP under explicit output alignment. Codec parsing, inverse
// quantization, modulation, windows and overlap still execute in AudioToolbox.
// Never linked or injected by decode-sq. This is a controlled hybrid reference,
// not the system decoder's default numerical path.
#include <Accelerate/Accelerate.h>
#include <pthread.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct { vDSP_DFT_Setup setup; vDSP_Length n; int direction; } Entry;
static Entry entries[128];
static pthread_mutex_t mutex = PTHREAD_MUTEX_INITIALIZER;
static unsigned long executions;
static int reference_mode; // 0: Float64 DIF, 1: vDSP aligned64, 2: vDSP offset16
static vDSP_DFT_Setup (*original_create)(vDSP_DFT_Setup, vDSP_Length, vDSP_DFT_Direction);
static void (*original_destroy)(vDSP_DFT_Setup);
static void (*original_execute)(const struct vDSP_DFT_SetupStruct *, const float *, const float *, float *, float *);

__attribute__((constructor)) static void initialize(void) {
    original_create = vDSP_DFT_zop_CreateSetup;
    original_destroy = vDSP_DFT_DestroySetup;
    original_execute = vDSP_DFT_Execute;
    const char *mode = getenv("APAC_REFERENCE_FFT_MODE");
    if (!mode || !strcmp(mode,"f64")) reference_mode=0;
    else if (!strcmp(mode,"aligned64")) reference_mode=1;
    else if (!strcmp(mode,"offset16")) reference_mode=2;
    else abort();
}
static vDSP_DFT_Setup create(vDSP_DFT_Setup previous, vDSP_Length n, vDSP_DFT_Direction direction) {
    vDSP_DFT_Setup setup = original_create(previous, n, direction);
    if (setup) {
        pthread_mutex_lock(&mutex);
        unsigned i;
        for (i=0; i<128 && entries[i].setup && entries[i].setup!=setup; ++i) {}
        if (i==128) abort();
        entries[i]=(Entry){setup,n,direction};
        pthread_mutex_unlock(&mutex);
    }
    return setup;
}
static void destroy(vDSP_DFT_Setup setup) {
    pthread_mutex_lock(&mutex);
    for (unsigned i=0;i<128;++i) if (entries[i].setup==setup) entries[i].setup=NULL;
    pthread_mutex_unlock(&mutex);
    original_destroy(setup);
}
static void execute(const struct vDSP_DFT_SetupStruct *setup, const float *ir, const float *ii, float *or_, float *oi) {
    Entry e={0};
    pthread_mutex_lock(&mutex);
    for (unsigned i=0;i<128;++i) if (entries[i].setup==setup) {e=entries[i];break;}
    ++executions;
    pthread_mutex_unlock(&mutex);
    if (!e.setup || (e.n!=64 && e.n!=512) || e.direction!=vDSP_DFT_FORWARD) {
        fputs("unverified DFT setup in SQ diagnostic reference\n",stderr);abort();
    }
    if (reference_mode) {
        // Alignment-only observation: the original vDSP performs all arithmetic.
        size_t stride=(e.n*sizeof(float)+63)&~(size_t)63;
        void *memory=NULL;
        if (posix_memalign(&memory,64,2*stride+64)) abort();
        size_t offset=reference_mode==2 ? 16 : 0;
        float *re=(float *)((char *)memory+offset);
        float *im=(float *)((char *)memory+stride+offset);
        original_execute(setup,ir,ii,re,im);
        memcpy(or_,re,e.n*sizeof(float));memcpy(oi,im,e.n*sizeof(float));
        free(memory);
        return;
    }
    double re[512],im[512];
    for (unsigned i=0;i<e.n;++i) {re[i]=ir[i];im[i]=ii[i];}
    // DIF butterflies followed by bit-reversed reads: independent organization
    // from the Rust DIT FFT. No gain change and no input/output address dispatch.
    for (unsigned size=(unsigned)e.n;size>=2;size>>=1) {
        unsigned half=size/2;
        for (unsigned start=0;start<e.n;start+=size) for (unsigned k=0;k<half;++k) {
            unsigned a=start+k,b=a+half;
            double dr=re[a]-re[b],di=im[a]-im[b];
            re[a]+=re[b];im[a]+=im[b];
            double angle=-2.*M_PI*k/size,c=cos(angle),s=sin(angle);
            re[b]=dr*c-di*s;im[b]=dr*s+di*c;
        }
    }
    for (unsigned i=0;i<e.n;++i) {
        unsigned rev=0,value=i;
        for (unsigned n=(unsigned)e.n;n>1;n>>=1) {rev=(rev<<1)|(value&1);value>>=1;}
        or_[i]=(float)re[rev];oi[i]=(float)im[rev];
    }
}
__attribute__((destructor)) static void finish(void) {
    const char *path=getenv("APAC_REFERENCE_FFT_AUDIT");
    if (path) {
        FILE *f=fopen(path,"wx");if(!f)abort();
        const char *method=reference_mode==1 ? "vdsp_aligned64" : reference_mode==2 ? "vdsp_offset16" : "f64_dif_round_f32";
        fprintf(f,"{\"method\":\"%s\",\"executions\":%lu}\n",method,executions);fclose(f);
    }
}
#define INTERPOSE(replacement, symbol) \
__attribute__((used,section("__DATA,__interpose"))) static const struct {const void *replacement,*original;} pair_##symbol={(void *)&replacement,(void *)&symbol};
INTERPOSE(create,vDSP_DFT_zop_CreateSetup)
INTERPOSE(execute,vDSP_DFT_Execute)
INTERPOSE(destroy,vDSP_DFT_DestroySetup)
