// owwfd_relay.cpp - PLAINTEXT transport relay at the ssl_stream boundary.
//
// Transport: wfd (0x298D6D8) -> byte-stream (wfd+0x80, vtable 0x298E2F8) -> ssl_stream at
// [bs+0x48] (vtable 0x298BED8). ssl_stream ABI: slot 0x10 = recv(this,dst,max,*out)->0 ok,
// slot 0x18 = send(this,buf,len,*out)->0 ok. bs.write hands PLAINTEXT to [bs+0x48].send.
//
// The byte-stream's send closure (0x21758a0) gates outbound on bs.state (slot1 [bs+0x20]) == 1.
// So: FORCE bs slot1 -> 1  => the client believes connected and writes PLAINTEXT (no TLS):
//   first a WebSocket upgrade "GET / HTTP/1.1 ... Upgrade: websocket", then BGS-over-WS frames.
// We swap [bs+0x48].send/recv to a byte-pipe: send -> our TCP socket to 127.0.0.1:1119 (the Go
// bnet server, --plaintext, which does the WS upgrade + BGS), recv <- that socket. TLS never runs.
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <cstdint>
#include <cstdio>
#include <cstdarg>
#include <cstring>
#include <string>
#include "gallery_roster.h"
#pragma comment(lib,"ws2_32.lib")
typedef uint64_t u64; typedef uint32_t u32; typedef uint16_t u16; typedef uint8_t u8; typedef int64_t i64;

static HINSTANCE g_module = nullptr;
static const u64 BS_VT=0x298E2F8ULL;          // byte-stream vtable
static const int SOCK_RECV_SLOT=2;            // [sockvt+0x10]
static const int SOCK_SEND_SLOT=3;            // [sockvt+0x18]
static const int VT_COPY=64;
static const u32 SRV_IP=0x7F000001;           // 127.0.0.1
static const u16 SRV_PORT=1119;
static const u16 RELAY_PORT=21119;            // always pipe the stripped plaintext here (our BGS WS server),
                                              // separate from the port the client dials (a hold-open stall)

static u64 g_base=0,g_imgHi=0; static FILE* g_log=nullptr; static CRITICAL_SECTION g_cs, g_mapCs;
static u64 g_bsVA=0, g_sockVtOrig=0; static u64* g_sockVtHeap=nullptr; static u64* g_bsVtHeap=nullptr;
static volatile LONG g_swaps=0,g_send=0,g_recv=0,g_stateCalls=0,g_wsaInit=0,g_relayErr=0;
static volatile ULONGLONG g_lastSwapTick=0;

// client-sock -> relay TCP socket (routed to the byte-stream's OWN target port)
struct Relay{ u64 cs; u16 port; SOCKET s; };
static Relay g_relays[64]; static int g_nRelays=0;

static void L(const char* f,...){char b[1600];va_list a;va_start(a,f);_vsnprintf(b,sizeof b-1,f,a);va_end(a);b[sizeof b-1]=0;EnterCriticalSection(&g_cs);if(g_log){fprintf(g_log,"[wfd] %s\n",b);fflush(g_log);}OutputDebugStringA("[wfd] ");OutputDebugStringA(b);OutputDebugStringA("\n");LeaveCriticalSection(&g_cs);}
static inline bool safe_read(void* addr, void* out, size_t size) {
    SIZE_T n = 0;
    return ReadProcessMemory(GetCurrentProcess(), addr, out, size, &n) && (n == size);
}
static inline bool safe_write(void* addr, const void* in, size_t size) {
    SIZE_T n = 0;
    return WriteProcessMemory(GetCurrentProcess(), addr, in, size, &n) && (n == size);
}
static u64 rd64(void* a, u64 dv = 0) {
#if defined(_MSC_VER) && !defined(__clang__)
    u64 r; __try { r = *(volatile u64*)a; } __except (EXCEPTION_EXECUTE_HANDLER) { return dv; } return r;
#else
    u64 r; if (!safe_read(a, &r, sizeof r)) return dv; return r;
#endif
}
static u32 rd32(void* a, u32 dv = 0) {
#if defined(_MSC_VER) && !defined(__clang__)
    u32 r; __try { r = *(volatile u32*)a; } __except (EXCEPTION_EXECUTE_HANDLER) { return dv; } return r;
#else
    u32 r; if (!safe_read(a, &r, sizeof r)) return dv; return r;
#endif
}
static inline bool inImage(u64 p){return g_base<=p&&p<g_imgHi;}
static inline bool inHeap(u64 p){return p>=0x0000000000010000ULL&&p<0x00007FF000000000ULL&&(p&7)==0;}
static void hexlog(const char* tag, const u8* p, u64 n) {
    if (n > 48) n = 48;
    u8 local_buf[48];
    if (!safe_read((void*)p, local_buf, (size_t)n)) return;
    char h[200]; u64 m = 0;
    for (u64 i = 0; i < n; i++) {
        int r = snprintf(h + m, sizeof h - m, "%02X ", local_buf[i]);
        if (r <= 0 || m > sizeof h - 4) break;
        m += r;
    }
    L("  %s[%llu]: %s", tag, (unsigned long long)n, h);
}

typedef int(*SockFn)(void* self, void* buf, u64 len, u64* out);

static int state_thunk(void* /*bs*/){ InterlockedIncrement(&g_stateCalls); return 1; }   // slot1: connected

// pre-register a client ssl_stream with the port IT targets (from [bs+0xa8]); connect lazily.
static void registerSock(u64 cs, u16 port){
    port=RELAY_PORT;  // ignore the client's target port; always pipe to our BGS server
    EnterCriticalSection(&g_mapCs);
    bool found=false;
    for(int i=0;i<g_nRelays;i++) if(g_relays[i].cs==cs){ found=true; break; }
    if(!found && g_nRelays<(int)(sizeof g_relays/sizeof g_relays[0])){
        g_relays[g_nRelays].cs=cs; g_relays[g_nRelays].port=port; g_relays[g_nRelays].s=INVALID_SOCKET; g_nRelays++;
    }
    LeaveCriticalSection(&g_mapCs);
}
// find the relay socket for a client ssl_stream; lazy-connect to 127.0.0.1:<its port>.
static SOCKET getRelay(u64 cs){
    SOCKET out=INVALID_SOCKET;
    EnterCriticalSection(&g_mapCs);
    for(int i=0;i<g_nRelays;i++) if(g_relays[i].cs==cs){
        if(g_relays[i].s==INVALID_SOCKET){
            if(!InterlockedCompareExchange(&g_wsaInit,1,0)){ WSADATA w; WSAStartup(MAKEWORD(2,2),&w); }
            SOCKET s=socket(AF_INET,SOCK_STREAM,IPPROTO_TCP);
            if(s!=INVALID_SOCKET){
                sockaddr_in a; memset(&a,0,sizeof a); a.sin_family=AF_INET; a.sin_port=htons(g_relays[i].port); a.sin_addr.s_addr=htonl(SRV_IP);
                if(connect(s,(sockaddr*)&a,sizeof a)==0){ g_relays[i].s=s;
                    L("*** relay CONNECTED to 127.0.0.1:%u for clientSock=%016llX ***",g_relays[i].port,(unsigned long long)cs); }
                else { L("relay connect() 127.0.0.1:%u failed %d",g_relays[i].port,WSAGetLastError()); closesocket(s); }
            } else L("relay socket() failed %d",WSAGetLastError());
        }
        out=g_relays[i].s; break;
    }
    LeaveCriticalSection(&g_mapCs);
    return out;
}

// send: pipe outbound plaintext to the relay socket
static int send_thunk(void* self, void* buf, u64 len, u64* out){
    LONG n=InterlockedIncrement(&g_send);
    SOCKET r=getRelay((u64)self);
    if(n<=8||(n%500)==0){ L("SEND #%ld self=%016llX len=%llu relay=%lld",n,(unsigned long long)self,(unsigned long long)len,(long long)r);
        if(inHeap((u64)buf)||inImage((u64)buf)) hexlog("send",(const u8*)buf,len); }
    if(r!=INVALID_SOCKET && (inHeap((u64)buf)||inImage((u64)buf))){
        u64 off=0; int guard=0;
        while(off<len && guard++<10000){ int s=send(r,(const char*)buf+off,(int)(len-off),0);
            if(s>0){ off+=s; } else { int e=WSAGetLastError(); if(e==WSAEWOULDBLOCK){ Sleep(0); continue; } L("  send() err %d",e); InterlockedIncrement(&g_relayErr); break; } }
    }
    if(out)*out=len; return 0;                       // report fully sent; TLS never runs
}

// recv: pipe inbound plaintext from the relay socket (short block so request/response lands)
static int recv_thunk(void* self, void* dst, u64 maxlen, u64* out){
    LONG n=InterlockedIncrement(&g_recv);
    SOCKET r=getRelay((u64)self);
    if(r==INVALID_SOCKET){ if(out)*out=0; return 0; }
    fd_set rd; FD_ZERO(&rd); FD_SET(r,&rd); timeval tv; tv.tv_sec=0; tv.tv_usec=30000; // 30ms
    int sel=select(0,&rd,nullptr,nullptr,&tv);
    if(sel>0 && FD_ISSET(r,&rd)){
        int got=recv(r,(char*)dst,(int)maxlen,0);
        if(got>0){ if(out)*out=got; if(n<=8||(n%500)==0){ L("RECV #%ld self=%016llX got=%d",n,(unsigned long long)self,got); hexlog("recv",(const u8*)dst,got);} return 0; }
        if(got==0){ if(out)*out=0; if(n<=8)L("RECV #%ld server closed",n); return 0; }
        int e=WSAGetLastError(); if(e!=WSAEWOULDBLOCK){ if(n<=8)L("RECV #%ld recv err %d",n,e); }
    }
    if(out)*out=0; return 0;                          // no data yet
}

static void ensureVts(u64 svt){
    if(g_sockVtHeap) return;
    g_sockVtOrig=svt;
    g_sockVtHeap=(u64*)VirtualAlloc(NULL,VT_COPY*8,MEM_COMMIT|MEM_RESERVE,PAGE_READWRITE);
    for(int i=0;i<VT_COPY;i++) g_sockVtHeap[i]=rd64((void*)(svt+i*8),0);
    g_sockVtHeap[SOCK_RECV_SLOT]=(u64)&recv_thunk;
    g_sockVtHeap[SOCK_SEND_SLOT]=(u64)&send_thunk;
    g_bsVtHeap=(u64*)VirtualAlloc(NULL,VT_COPY*8,MEM_COMMIT|MEM_RESERVE,PAGE_READWRITE);
    for(int i=0;i<VT_COPY;i++) g_bsVtHeap[i]=rd64((void*)(g_bsVA+i*8),0);
    g_bsVtHeap[1]=(u64)&state_thunk;               // byte-stream slot1 (state) -> 1
    L("built vts: sockHeap=%p (from %llX rva %llX) bsHeap=%p",(void*)g_sockVtHeap,(unsigned long long)svt,(unsigned long long)(svt-g_base),(void*)g_bsVtHeap);
}

static void scan(){
    SYSTEM_INFO si;GetSystemInfo(&si);u64 a=(u64)si.lpMinimumApplicationAddress,mx=(u64)si.lpMaximumApplicationAddress;MEMORY_BASIC_INFORMATION m;
    alignas(u64) static u8 page_buf[65536];
    static bool g_fault_logged = false;
    while(a<mx&&VirtualQuery((void*)a,&m,sizeof m)){
        u64 rb=(u64)m.BaseAddress,rs=m.RegionSize;
        if(m.State==MEM_COMMIT&&m.Type==MEM_PRIVATE&&inHeap(rb)&&(m.Protect&(PAGE_READWRITE|PAGE_EXECUTE_READWRITE))&&!(m.Protect&PAGE_GUARD)&&rs<=0x8000000){
            for (u64 p = rb; p + 0xC0 <= rb + rs; ) {
                SIZE_T to_read = sizeof(page_buf);
                if (p + to_read > rb + rs) to_read = (SIZE_T)((rb + rs) - p);
                SIZE_T nread = 0;
                if (!ReadProcessMemory(GetCurrentProcess(), (void*)p, page_buf, to_read, &nread) || nread < 0xC0) {
                    to_read = 4096;
                    if (p + to_read > rb + rs) to_read = (SIZE_T)((rb + rs) - p);
                    if (!ReadProcessMemory(GetCurrentProcess(), (void*)p, page_buf, to_read, &nread) || nread < 0xC0) {
                        DWORD err = GetLastError();
                        if (!g_fault_logged) {
                            MEMORY_BASIC_INFORMATION mbi = {};
                            VirtualQuery((void*)p, &mbi, sizeof(mbi));
                            u64 fault_addr = (mbi.Protect & (PAGE_READWRITE | PAGE_EXECUTE_READWRITE))
                                ? ((u64)mbi.BaseAddress + mbi.RegionSize)
                                : (u64)mbi.BaseAddress;
                            L("[scan-fault] addr=%016llX err=%lu mbi.Base=%016llX mbi.Size=%llX state=%lx prot=%lx",
                              (unsigned long long)fault_addr, err,
                              (unsigned long long)mbi.BaseAddress, (unsigned long long)mbi.RegionSize,
                              mbi.State, mbi.Protect);
                            g_fault_logged = true;
                        }
                        p = ((p + 4096ULL) & ~4095ULL);
                        continue;
                    }
                }
                size_t off = 0;
                for (; off + 0xC0 <= nread; off += 8) {
                    u64 vt = 0;
                    memcpy(&vt, page_buf + off, sizeof(vt));
                    if (vt != g_bsVA) continue;                       // only unswapped byte-streams
                    u64 bs = p + off; u64 sock = rd64((void*)(bs + 0x48), 0);
                    if (!inHeap(sock)) continue;
                    u64 svt = rd64((void*)sock, 0);
                    if (!inImage(svt)) continue;
                    if (g_sockVtHeap && svt == (u64)g_sockVtHeap) continue;   // sock already swapped
                    if (g_sockVtOrig && svt != g_sockVtOrig) { continue; }     // different sock class - skip
                    LONG sn = InterlockedIncrement(&g_swaps);
                    g_lastSwapTick = GetTickCount64();
                    u16 port = (u16)(rd32((void*)(bs + 0xa8)) & 0xFFFF);
                    char host[80] = {0}; u64 hp = rd64((void*)(bs + 0x80), 0), hl = rd64((void*)(bs + 0x88), 0);
                    if (hl >= sizeof(host)) hl = sizeof(host) - 1;
                    void* src = (void*)(inHeap(hp) ? hp : (bs + 0x80));
                    if (safe_read(src, host, hl)) {
                        for (u64 i = 0; i < hl; i++) {
                            if ((u8)host[i] < 32 || (u8)host[i] >= 127) host[i] = '.';
                        }
                    }
                    u32 st = rd32((void*)(bs + 0x20));
                    if (sn <= 20 || port != 1119) {     // ALWAYS log a non-1119 (e.g. lobby 3724) byte-stream
                        L("BYTE-STREAM bs=%016llX sock=%016llX sockVt(rva %llX) state=%u host='%s' port=%u swaps=%ld",
                          (unsigned long long)bs, (unsigned long long)sock, (unsigned long long)(svt - g_base), st, host, port, sn);
                    }
                    registerSock((u64)sock, port);
                    ensureVts(svt);
#if defined(_MSC_VER) && !defined(__clang__)
                    __try { *(volatile u64*)sock = (u64)g_sockVtHeap; } __except (EXCEPTION_EXECUTE_HANDLER) {}
                    __try { *(volatile u64*)bs = (u64)g_bsVtHeap; } __except (EXCEPTION_EXECUTE_HANDLER) {}
#else
                    u64 hs_val = (u64)g_sockVtHeap; safe_write((void*)sock, &hs_val, sizeof hs_val);
                    u64 hbs_val = (u64)g_bsVtHeap;  safe_write((void*)bs, &hbs_val, sizeof hbs_val);
#endif
                }
                if (nread == to_read) {
                    p += (off > 0 ? off : 8);
                } else {
                    p += (to_read > 4096 ? 4096 : to_read);
                }
            }
        }
        a = rb + rs;
    }
}
extern "C" __declspec(dllexport) DWORD WINAPI worker(LPVOID){
    static volatile LONG s_started = 0;
    if (InterlockedCompareExchange(&s_started, 1, 0) != 0) return 0;
    InitializeCriticalSection(&g_cs);InitializeCriticalSection(&g_mapCs);
    wchar_t modulePath[32768] = {};
    DWORD pathLength = GetModuleFileNameW(g_module, modulePath, 32768);
    if (pathLength > 0 && pathLength < 32768) {
        std::wstring directory(modulePath, pathLength);
        const auto slash = directory.find_last_of(L"\\/");
        if (slash != std::wstring::npos) {
            directory = directory.substr(0, slash) + L"\\log";
            CreateDirectoryW(directory.c_str(), nullptr);
            const std::wstring logPath = directory + L"\\wfd.log";
            g_log = _wfopen(logPath.c_str(), L"a");
        }
    }
    g_base=(u64)GetModuleHandleW(L"Overwatch.exe");if(!g_base)g_base=(u64)GetModuleHandleW(NULL);
    IMAGE_DOS_HEADER* dos=(IMAGE_DOS_HEADER*)g_base;IMAGE_NT_HEADERS* nt=(IMAGE_NT_HEADERS*)(g_base+dos->e_lfanew);g_imgHi=g_base+nt->OptionalHeader.SizeOfImage;
    g_bsVA=g_base+BS_VT;
    L("==== owwfd_relay (plaintext pipe -> 127.0.0.1:%u) base=%016llX bsVt=%016llX ====",SRV_PORT,(unsigned long long)g_base,(unsigned long long)g_bsVA);
    auto gallery = gallery_roster::Install::waiting;
    for(int loop=0;;loop++){
        if (gallery == gallery_roster::Install::waiting && loop % 20 == 0) {
            gallery = gallery_roster::install(g_base, g_imgHi);
            if (gallery != gallery_roster::Install::waiting)
                L("[gallery] NPC roster extension %s",
                  gallery == gallery_roster::Install::installed ? "installed" : "failed");
        }
        scan();
        if((loop%80)==0)L("[poll] loop=%d swaps=%ld send=%ld recv=%ld stateCalls=%ld relays=%d err=%ld",loop,g_swaps,g_send,g_recv,g_stateCalls,g_nRelays,g_relayErr);
        ULONGLONG now = GetTickCount64();
        bool recent = (g_swaps > 0) && (now - g_lastSwapTick < 4000);
        Sleep((g_swaps > 0 && !recent) ? 50 : 8);
    }
    return 0;
}
BOOL WINAPI DllMain(HINSTANCE h,DWORD reason,LPVOID){if(reason==DLL_PROCESS_ATTACH){g_module=h;DisableThreadLibraryCalls(h);HANDLE t=CreateThread(nullptr,0,worker,nullptr,0,nullptr);if(t)CloseHandle(t);}return TRUE;}
