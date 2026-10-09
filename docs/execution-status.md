# 执行进度

## 当前状态

- ACTIVE_OBJECT：78boot 无 Manager 的固定脚本 root 控制方案。
- LAST_CONFIRMED_RESULT：主机执行入口 19 项验证通过；真实签名工具 19 项副本验证通过；原始固件和目标脚本哈希保持不变。
- NEXT_EXECUTABLE_ACTION：在 GitHub Actions 构建固定 commit 的 LKM 与 userspace，修补 init_boot 副本，使用指定工具签名并下载经过主机验证的候选。
- INPUT_PATHS：`config/upstreams.lock.json`、`config/no-manager.config`、`ci/stock/init_boot.img`、`ci/stock/vbmeta.img`、`tools/avb/`、Actions Secrets。
- ACCEPTANCE_EVENT：Actions 构建成功且签名 artifact 下载后哈希校验通过。设备开机属于独立的后续验收。

## 已执行

1. `adb devices -l` 与 `fastboot devices` 未发现设备；`verification/stage0/device.json` 明确记录 `no-authorized-device`，退出码 3。
2. 从本地 boot 提取真实 banner 与 IKCONFIG：Android 15、6.6.89-android15-8、AArch64、4K、header v4、启用 KPROBES/MODULES/MODVERSIONS/SELinux。保存完整配置和镜像哈希。
3. 发现随包 vmlinux 的 banner/config 哈希与 boot 不同；构建以固定上游的 android15-6.6 DDK 路径为候选，不能假定随包 vmlinux 完全匹配。
4. 获取 KernelSU 与 KernelSU-Next，分别固定 `df03912f70d92ff2aa9762ef82d607033d37e1da`、`8d41fffa5dd791d5f25eb279185342d60a0f42f2`。两者均具有 CONFIG_KSU_DISABLE_MANAGER；首选 KernelSU LKM，不开启 debug/shell 授权，不关闭 policy profiles。
5. 从原始脚本第 84 行开始在内存读取压缩包，验证脚本与两 ELF 哈希：主程序为 Android 动态 AArch64，使用 linker64 和 libandroid/liblog/libm/libdl/libc；helper 为无 PT_INTERP/DT_NEEDED 的 AArch64 ELF。未执行载荷。
6. 保留用户签名 RAR，解包并固定签名源码/密钥哈希。原始工具代码不变，仅通过适配层使用主机 Python 与 OpenSSL。
7. 验证链式 boot、普通 init_boot+vbmeta、无 footer 修补结果的 metadata 准备、错误分区/缺少 vbmeta/输出覆盖拒绝、签名前后与回滚的字节和哈希。
8. 原工具会追加相同属性描述符；首轮严格按个数检查失败。诊断记录在 `verification/signing-diagnostic.log`；现允许内容相同的属性重复，仍检查所有其他描述符和签名。首轮失败命令退出码 1；修正后集成测试全部通过。

## 设备相关未完成项

尚无设备的运行版本、活动槽位、bootloader 状态、真实 UID/capabilities/SELinux、root 首次部署和载荷运行记录。Actions 镜像只作为构建候选。内核模块加载成功后，还需建立 `/data/adb/ksud` 及固定脚本的可信部署路径；不把编译成功或 AVB 校验通过称为已经取得可用的脚本 root。

计划阶段 1 的设备基线尚未完成；完整 `check/run/status/logs`、SELinux 调整、开机启动、升级卸载依赖该基线。现有 `root-control 1|2` 与安装入口保留。

## 固定的四个验收角色

- MODIFIED_FILE：`/Users/a77/Desktop/git/78boot/bin/root-control`
- DIFF_FILE：`/Users/a77/Desktop/git/78boot/verification/root-control.patch`
- VERIFICATION：`/Users/a77/Desktop/git/78boot/verification/VERIFICATION.txt`
- ROLLBACK：`/Users/a77/Desktop/git/78boot/scripts/ROLLBACK.sh`

本次主机复验观察：BASELINE 为入口不存在，shell 退出 127（macOS）；MODIFIED 执行无害 fixture，stdout 为 `fixture mode=1 secret=unset`，退出 7；ROLLBACK 删除入口副本，恢复不存在状态，退出 127。Android UID/root 所有权为明确的主机模拟。这四个角色继续复用；签名证据与 Actions 记录是补充。
