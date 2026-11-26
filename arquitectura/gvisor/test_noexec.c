// test_noexec.c — verifica si gVisor aplica "noexec" al mmap ejecutable (PROT_EXEC).
//
// La pregunta de Fable: ¿gVisor bloquea mmap(PROT_EXEC) sobre un archivo en un mount con
// "noexec", o solo bloquea execve()? Esto importa para codeStream, que dlopen() un .so
// (dlopen usa mmap(PROT_EXEC) internamente, no execve).
//
// El programa:
//   1. escribe un byte en el archivo dado como argv[1].
//   2. intenta mmap(PROT_EXEC) sobre ese archivo.
//   3. imprime si tuvo éxito o el error.
//
// Uso: test_noexec <archivo_a_probar>

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/mman.h>
#include <errno.h>

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "uso: %s <archivo>\n", argv[0]);
        return 2;
    }
    const char *path = argv[1];
    int fd = open(path, O_CREAT | O_RDWR, 0644);
    if (fd < 0) {
        printf("RESULT: open fallo (%s): %s\n", path, strerror(errno));
        return 1;
    }
    // escribir algo (x64 NOP + ret = 0x90 0xC3)
    unsigned char code[] = {0x90, 0xC3};
    if (write(fd, code, sizeof(code)) != (ssize_t)sizeof(code)) {
        printf("RESULT: write fallo: %s\n", strerror(errno));
        close(fd);
        return 1;
    }
    // intentar mapear el archivo con PROT_EXEC
    void *p = mmap(NULL, sizeof(code), PROT_READ | PROT_EXEC, MAP_PRIVATE, fd, 0);
    if (p == MAP_FAILED) {
        printf("RESULT: mmap(PROT_EXEC) BLOQUEADO en %s: %s (errno=%d)\n",
               path, strerror(errno), errno);
        close(fd);
        return 1;
    }
    printf("RESULT: mmap(PROT_EXEC) PERMITIDO en %s (p=%p)\n", path, p);
    munmap(p, sizeof(code));
    close(fd);
    return 0;
}
