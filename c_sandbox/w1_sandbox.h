/**
 * W1™ NEXUS — Native C Aerospace Sandbox Core (w1_sandbox.h)
 * 
 * High-performance, non-constraining process containment and resource boundary engine.
 * Protects host operating system integrity via Win32 Job Objects on Windows
 * and POSIX rlimits on Unix, while guaranteeing zero artificial throttling or
 * token truncation of model reasoning and computational workflows.
 * 
 * Licensed under Apache-2.0
 */

#ifndef W1_SANDBOX_H
#define W1_SANDBOX_H

#include <stdint.h>
#include <stddef.h>

#ifdef _WIN32
  #define W1_EXPORT __declspec(dllexport)
#else
  #define W1_EXPORT __attribute__((visibility("default")))
#endif

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Execution configuration for the sandboxed process.
 * Designed to provide unconstrained cognitive and execution freedom
 * while preventing OS crash vectors (runaway fork-bombs, memory exhaustion, hangs).
 */
typedef struct {
    uint32_t timeout_ms;          /* Wall-clock deadline in milliseconds (0 = no limit) */
    uint64_t memory_limit_bytes;  /* Max memory envelope in bytes (e.g. 2GB–4GB) */
    uint32_t max_processes;       /* Max concurrent child processes (prevents fork-bombs) */
    int32_t  unconstrained_cpu;   /* 1 = high/normal CPU priority, no artificial throttling */
    const char* working_dir;      /* Isolated execution directory (NULL = current) */
} W1SandboxConfig;

/**
 * Execution result returned from the sandboxed process.
 */
typedef struct {
    int32_t  exit_code;           /* Process exit code (-1 on timeout/abort) */
    int32_t  timed_out;           /* 1 if process exceeded timeout_ms, else 0 */
    int32_t  memory_exceeded;     /* 1 if process exceeded memory envelope, else 0 */
    uint64_t peak_memory_bytes;   /* Peak memory consumed during execution */
    uint64_t elapsed_ms;          /* Total wall-clock execution duration in ms */
    char*    stdout_buffer;       /* Full stdout stream (dynamically allocated) */
    uint64_t stdout_len;          /* Length of stdout stream */
    char*    stderr_buffer;       /* Full stderr stream (dynamically allocated) */
    uint64_t stderr_len;          /* Length of stderr stream */
} W1SandboxResult;

/**
 * Executes a command within the native aerospace containment envelope.
 * 
 * @param command The shell/executable command line to execute.
 * @param config  Configuration specifying resource envelopes.
 * @return Dynamically allocated W1SandboxResult pointer. Must be freed via w1_sandbox_free_result.
 */
W1_EXPORT W1SandboxResult* w1_sandbox_execute(const char* command, const W1SandboxConfig* config);

/**
 * Frees memory allocated for a W1SandboxResult.
 */
W1_EXPORT void w1_sandbox_free_result(W1SandboxResult* result);

/**
 * Diagnostics and capability probe for native sandbox features.
 * 
 * @return JSON string containing platform features (Win32 Job Objects, rlimit, etc.).
 *         Must be freed via w1_sandbox_free_string.
 */
W1_EXPORT char* w1_sandbox_diagnose(void);

/**
 * Frees a string allocated by w1_sandbox_diagnose.
 */
W1_EXPORT void w1_sandbox_free_string(char* str);

#ifdef __cplusplus
}
#endif

#endif /* W1_SANDBOX_H */
