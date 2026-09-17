#include <windows.h>

void* __stack_chk_guard = (void*)0x595e9fbd94fda766;

void __stack_chk_fail(void) {
    FatalAppExitA(0, "Stack check failed!");
}
