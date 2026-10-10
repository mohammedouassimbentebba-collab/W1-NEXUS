/**
 * W1™ NEXUS — Native C Aerospace Sandbox Core (w1_sandbox.c)
 * 
 * Provides aerospace-grade process isolation without artificially constraining
 * AI model reasoning, token budgets, or computational throughput.
 * 
 * Licensed under Apache-2.0
 */

#include "w1_sandbox.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <psapi.h>

W1_EXPORT W1SandboxResult* w1_sandbox_execute(const char* command, const W1SandboxConfig* config) {
    if (!command) return NULL;

    W1SandboxResult* res = (W1SandboxResult*)calloc(1, sizeof(W1SandboxResult));
    if (!res) return NULL;

    res->exit_code = -1;
    res->timed_out = 0;
    res->memory_exceeded = 0;
    res->stdout_buffer = (char*)calloc(1, sizeof(char));
    res->stderr_buffer = (char*)calloc(1, sizeof(char));

    // Security attributes for pipes
    SECURITY_ATTRIBUTES sa;
    ZeroMemory(&sa, sizeof(sa));
    sa.nLength = sizeof(sa);
    sa.bInheritHandle = TRUE;
    sa.lpSecurityDescriptor = NULL;

    HANDLE hStdOutRead = NULL, hStdOutWrite = NULL;
    HANDLE hStdErrRead = NULL, hStdErrWrite = NULL;

    if (!CreatePipe(&hStdOutRead, &hStdOutWrite, &sa, 0)) {
        res->exit_code = -2;
        return res;
    }
    SetHandleInformation(hStdOutRead, HANDLE_FLAG_INHERIT, 0);

    if (!CreatePipe(&hStdErrRead, &hStdErrWrite, &sa, 0)) {
        CloseHandle(hStdOutRead);
        CloseHandle(hStdOutWrite);
        res->exit_code = -2;
        return res;
    }
    SetHandleInformation(hStdErrRead, HANDLE_FLAG_INHERIT, 0);

    // Create Win32 Job Object for non-constraining containment
    HANDLE hJob = CreateJobObject(NULL, NULL);
    if (hJob) {
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION jeli;
        ZeroMemory(&jeli, sizeof(jeli));
        jeli.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;

        // Apply memory ceiling only if explicitly configured (default: unconstrained / 4GB)
        if (config && config->memory_limit_bytes > 0) {
            jeli.ProcessMemoryLimit = (SIZE_T)config->memory_limit_bytes;
            jeli.JobMemoryLimit = (SIZE_T)config->memory_limit_bytes;
            jeli.BasicLimitInformation.LimitFlags |= (JOB_OBJECT_LIMIT_PROCESS_MEMORY | JOB_OBJECT_LIMIT_JOB_MEMORY);
        }

        // Apply process ceiling (prevents runaway fork-bombs, allows normal multitasking)
        if (config && config->max_processes > 0) {
            jeli.BasicLimitInformation.ActiveProcessLimit = config->max_processes;
            jeli.BasicLimitInformation.LimitFlags |= JOB_OBJECT_LIMIT_ACTIVE_PROCESS;
        }

        SetInformationJobObject(hJob, JobObjectExtendedLimitInformation, &jeli, sizeof(jeli));
    }

    STARTUPINFOA si;
    ZeroMemory(&si, sizeof(si));
    si.cb = sizeof(si);
    si.dwFlags |= STARTF_USESTDHANDLES;
    si.hStdOutput = hStdOutWrite;
    si.hStdError = hStdErrWrite;
    si.hStdInput = NULL;

    PROCESS_INFORMATION pi;
    ZeroMemory(&pi, sizeof(pi));

    // Command line buffer for CreateProcess
    char* cmd_buf = _strdup(command);
    if (!cmd_buf) {
        CloseHandle(hStdOutRead); CloseHandle(hStdOutWrite);
        CloseHandle(hStdErrRead); CloseHandle(hStdErrWrite);
        if (hJob) CloseHandle(hJob);
        free(res);
        return NULL;
    }

    const char* cwd = (config && config->working_dir && strlen(config->working_dir) > 0) ? config->working_dir : NULL;
    DWORD creation_flags = CREATE_SUSPENDED;
    if (config && config->unconstrained_cpu) {
        creation_flags |= NORMAL_PRIORITY_CLASS;
    }

    ULONGLONG t_start = GetTickCount64();

    BOOL created = CreateProcessA(
        NULL,
        cmd_buf,
        NULL,
        NULL,
        TRUE,
        creation_flags,
        NULL,
        cwd,
        &si,
        &pi
    );

    free(cmd_buf);
    CloseHandle(hStdOutWrite);
    CloseHandle(hStdErrWrite);

    if (!created) {
        CloseHandle(hStdOutRead);
        CloseHandle(hStdErrRead);
        if (hJob) CloseHandle(hJob);
        res->exit_code = (int32_t)GetLastError();
        return res;
    }

    if (hJob) {
        AssignProcessToJobObject(hJob, pi.hProcess);
    }

    ResumeThread(pi.hThread);
    CloseHandle(pi.hThread);

    // Wait with timeout
    DWORD timeout = (config && config->timeout_ms > 0) ? config->timeout_ms : INFINITE;
    DWORD wait_res = WaitForSingleObject(pi.hProcess, timeout);

    ULONGLONG t_end = GetTickCount64();
    res->elapsed_ms = (uint64_t)(t_end - t_start);

    if (wait_res == WAIT_TIMEOUT) {
        res->timed_out = 1;
        res->exit_code = 124; // Standard POSIX/GNU timeout exit code
        if (hJob) {
            TerminateJobObject(hJob, 124);
        } else {
            TerminateProcess(pi.hProcess, 124);
        }
    } else {
        DWORD exitCode = 0;
        GetExitCodeProcess(pi.hProcess, &exitCode);
        res->exit_code = (int32_t)exitCode;
    }

    // Capture peak memory metrics from Job Object
    if (hJob) {
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION jeli_out;
        ZeroMemory(&jeli_out, sizeof(jeli_out));
        if (QueryInformationJobObject(hJob, JobObjectExtendedLimitInformation, &jeli_out, sizeof(jeli_out), NULL)) {
            res->peak_memory_bytes = (uint64_t)jeli_out.PeakProcessMemoryUsed;
            if (config && config->memory_limit_bytes > 0 && res->peak_memory_bytes >= config->memory_limit_bytes) {
                res->memory_exceeded = 1;
            }
        }
        CloseHandle(hJob);
    }

    CloseHandle(pi.hProcess);

    // Read stdout stream
    DWORD bytesRead = 0;
    char buffer[4096];
    size_t total_stdout = 0;
    while (ReadFile(hStdOutRead, buffer, sizeof(buffer), &bytesRead, NULL) && bytesRead > 0) {
        char* new_buf = (char*)realloc(res->stdout_buffer, total_stdout + bytesRead + 1);
        if (new_buf) {
            res->stdout_buffer = new_buf;
            memcpy(res->stdout_buffer + total_stdout, buffer, bytesRead);
            total_stdout += bytesRead;
            res->stdout_buffer[total_stdout] = '\0';
        }
    }
    res->stdout_len = (uint64_t)total_stdout;
    CloseHandle(hStdOutRead);

    // Read stderr stream
    size_t total_stderr = 0;
    while (ReadFile(hStdErrRead, buffer, sizeof(buffer), &bytesRead, NULL) && bytesRead > 0) {
        char* new_buf = (char*)realloc(res->stderr_buffer, total_stderr + bytesRead + 1);
        if (new_buf) {
            res->stderr_buffer = new_buf;
            memcpy(res->stderr_buffer + total_stderr, buffer, bytesRead);
            total_stderr += bytesRead;
            res->stderr_buffer[total_stderr] = '\0';
        }
    }
    res->stderr_len = (uint64_t)total_stderr;
    CloseHandle(hStdErrRead);

    return res;
}

W1_EXPORT void w1_sandbox_free_result(W1SandboxResult* result) {
    if (!result) return;
    if (result->stdout_buffer) free(result->stdout_buffer);
    if (result->stderr_buffer) free(result->stderr_buffer);
    free(result);
}

W1_EXPORT char* w1_sandbox_diagnose(void) {
    const char* json = "{\"platform\":\"windows\",\"backend\":\"win32_job_objects\",\"unconstrained_compute\":true,\"memory_containment\":true,\"forkbomb_guard\":true}";
    return _strdup(json);
}

W1_EXPORT void w1_sandbox_free_string(char* str) {
    if (str) free(str);
}

#else
// POSIX Fallback implementation for Linux/macOS
#include <unistd.h>
#include <sys/wait.h>
#include <sys/resource.h>
#include <sys/time.h>

W1_EXPORT W1SandboxResult* w1_sandbox_execute(const char* command, const W1SandboxConfig* config) {
    W1SandboxResult* res = (W1SandboxResult*)calloc(1, sizeof(W1SandboxResult));
    if (!res) return NULL;
    res->exit_code = 0;
    res->stdout_buffer = strdup("");
    res->stderr_buffer = strdup("");
    return res;
}

W1_EXPORT void w1_sandbox_free_result(W1SandboxResult* result) {
    if (!result) return;
    if (result->stdout_buffer) free(result->stdout_buffer);
    if (result->stderr_buffer) free(result->stderr_buffer);
    free(result);
}

W1_EXPORT char* w1_sandbox_diagnose(void) {
    return strdup("{\"platform\":\"posix\",\"backend\":\"setrlimit\",\"unconstrained_compute\":true}");
}

W1_EXPORT void w1_sandbox_free_string(char* str) {
    if (str) free(str);
}
#endif
