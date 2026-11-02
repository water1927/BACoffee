#define UNICODE
#define _UNICODE
#include <windows.h>
#include <wchar.h>
/* Unicode-only native launcher. Forward the original argument tail unchanged,
   with an explicit application path; never invoke a shell or depend on cwd. */
int WINAPI WinMain(HINSTANCE instance,HINSTANCE previous,LPSTR ignored,int show) {
    wchar_t root[32768],host[32768],command[32768];
    DWORD n=GetModuleFileNameW(NULL,root,32768);
    if (!n || n>=32768) return 2;
    wchar_t *slash=wcsrchr(root,L'\\');
    if (!slash) return 2;
    *slash=0;
    if (wcslen(root)+64>=32768) return 2;
    wcscpy(host,root);wcscat(host,L"\\BACoffee.exe");
    const wchar_t *tail=GetCommandLineW();
    if (*tail==L'"') { ++tail; while (*tail && *tail!=L'"') ++tail; if (*tail) ++tail; }
    else { while (*tail && *tail!=L' ' && *tail!=L'\t') ++tail; }
    while (*tail==L' ' || *tail==L'\t') ++tail;
    if (wcslen(host)+wcslen(tail)+32>=32768) return 2;
    wcscpy(command,L"\"");wcscat(command,host);wcscat(command,L"\" --sat ");wcscat(command,tail);
    STARTUPINFOW startup={0};PROCESS_INFORMATION process={0};startup.cb=sizeof(startup);
    if (!CreateProcessW(host,command,NULL,NULL,FALSE,0,NULL,root,&startup,&process)) {
        MessageBoxW(NULL,L"Cannot start BACoffee shared host. Keep both entry points in the complete program folder.",L"BACoffee SAT",MB_OK|MB_ICONERROR);
        return 2;
    }
    CloseHandle(process.hThread);
    WaitForSingleObject(process.hProcess,INFINITE);
    DWORD code=2;GetExitCodeProcess(process.hProcess,&code);CloseHandle(process.hProcess);
    return (int)code;
}
