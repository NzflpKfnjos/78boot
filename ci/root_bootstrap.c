/* Fixed-purpose boot service. The executable is a signed init_boot ramdisk member. */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>
#include <android/log.h>
#include "uapi/supercall.h"
#include "uapi/feature.h"
#include "autostart_assets.h"

#define BASE "/data/adb/root-control"

static int failed(const char *operation)
{
    __android_log_print(ANDROID_LOG_ERROR, "RootControl", "%s: %s", operation, strerror(errno));
    return 1;
}

static int ensure_dir(const char *path)
{
    struct stat info;
    if (mkdir(path, 0700) && errno != EEXIST)
        return -1;
    if (lstat(path, &info) || !S_ISDIR(info.st_mode) || info.st_uid != 0)
        return -1;
    /* Existing /data/adb may be shared with upstream. Do not rewrite its mode. */
    if (!strcmp(path, "/data/adb"))
        return info.st_gid == 0 && (info.st_mode & 0777) == 0700 ? 0 : -1;
    return chmod(path, 0700);
}

static int write_file(const char *path, const unsigned char *data, size_t size, mode_t mode)
{
    char temporary[512];
    int n = snprintf(temporary, sizeof(temporary), "%s.new", path);
    if (n < 0 || (size_t)n >= sizeof(temporary))
        return -1;
    if (unlink(temporary) && errno != ENOENT)
        return -1;
    int fd = open(temporary, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
    if (fd < 0)
        return -1;
    size_t done = 0;
    while (done < size) {
        ssize_t count = write(fd, data + done, size - done);
        if (count < 0 && errno == EINTR)
            continue;
        if (count <= 0) {
            close(fd);
            unlink(temporary);
            return -1;
        }
        done += (size_t)count;
    }
    int result = fchown(fd, 0, 0) || fchmod(fd, mode) || fsync(fd);
    if (close(fd))
        result = -1;
    if (!result)
        result = rename(temporary, path);
    if (result)
        unlink(temporary);
    return result;
}

static int label_files(void)
{
    pid_t child = fork();
    if (child < 0)
        return -1;
    if (!child) {
        execl("/system/bin/toybox", "toybox", "chcon", "-R", "u:object_r:ksu_file:s0", BASE, (char *)NULL);
        _exit(127);
    }
    int status;
    while (waitpid(child, &status, 0) < 0)
        if (errno != EINTR)
            return -1;
    return WIFEXITED(status) && WEXITSTATUS(status) == 0 ? 0 : -1;
}

static int configure_kernel(void)
{
    int fd = -1;
    /* The upstream reboot hook installs an fd; these magic values do not reboot. */
    (void)syscall(SYS_reboot, KSU_INSTALL_MAGIC1, KSU_INSTALL_MAGIC2, 0, &fd);
    if (fd < 0)
        return -1;
    struct ksu_set_feature_cmd feature = {.feature_id = KSU_FEATURE_SU_COMPAT, .value = 0};
    int result = ioctl(fd, KSU_IOCTL_SET_FEATURE, &feature);
    feature.feature_id = KSU_FEATURE_SELINUX_HIDE;
    feature.value = 1;
    if (!result)
        result = ioctl(fd, KSU_IOCTL_SET_FEATURE, &feature);
    struct ksu_report_event_cmd event = {.event = EVENT_POST_FS_DATA};
    if (!result)
        result = ioctl(fd, KSU_IOCTL_REPORT_EVENT, &event);
    close(fd);
    return result;
}

int main(int argc, char **argv)
{
    (void)argv;
    if (argc != 1 || getuid() != 0) {
        errno = EPERM;
        return failed("root-only preparation required");
    }
    umask(077);
    if (ensure_dir("/data/adb") || ensure_dir(BASE) || ensure_dir(BASE "/bin") ||
        ensure_dir(BASE "/scripts") || ensure_dir(BASE "/logs") || ensure_dir(BASE "/state"))
        return failed("prepare private directories");
    unlink(BASE "/state/prepared");
    if (configure_kernel())
        return failed("configure root isolation");
    if (write_file(BASE "/scripts/1_no_login.sh", payload_script, payload_script_len, 0400) ||
        write_file(BASE "/bin/root-control", runner_script, runner_script_len, 0500) ||
        write_file(BASE "/bin/autostart.sh", boot_script, boot_script_len, 0400))
        return failed("install pinned boot assets");
    if (label_files())
        return failed("label private boot assets");
    unsigned char boot_id[80];
    int fd = open("/proc/sys/kernel/random/boot_id", O_RDONLY | O_CLOEXEC);
    if (fd < 0)
        return failed("read boot identity");
    ssize_t count = read(fd, boot_id, sizeof(boot_id));
    close(fd);
    if (count <= 0 || count >= (ssize_t)sizeof(boot_id))
        return failed("read boot identity");
    if (write_file(BASE "/state/prepared", boot_id, (size_t)count, 0600) || label_files())
        return failed("publish preparation state");
    __android_log_print(ANDROID_LOG_INFO, "RootControl", "private assets prepared; app root grants disabled; mode=1");
    return 0;
}
