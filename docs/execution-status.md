# 执行进度

## 当前状态

- ACTIVE_OBJECT：78boot 无 Manager 的固定脚本 root 控制方案。
- LAST_CONFIRMED_RESULT：内嵌候选 Actions 37962844664 四任务成功；下载的 init_boot 中已验证完整脚本与启动组件字节一致、签名及回滚通过；8 项自动启动 fixture 检查及原生授权函数测试通过；真实 Android shell 调用 bootstrap 被拒绝，未执行载荷。
- NEXT_EXECUTABLE_ACTION：用户手动 9008 将新签名 init_boot/vbmeta 写入 A 槽并开机后，读取 RootControl 日志确认准备、模式 1 自动启动及退出结果，再复测检测软件。
- INPUT_PATHS：`out/autostart-37962844664/`、`verification/autostart-artifact-verification.json`、`verification/autostart-actions-result.json`、`verification/autostart-device-authorization.json`。
- ACCEPTANCE_EVENT：新镜像设备重启后记录固定脚本模式 1 的真实启动结果，并保存普通 app root/SELinux 探测复测。当前镜像交付和主机验收完成；手动刷入按用户要求保留给用户。

## 已执行

1. `adb devices -l` 与 `fastboot devices` 未发现设备；`verification/stage0/device.json` 明确记录 `no-authorized-device`，退出码 3。
2. 从本地 boot 提取真实 banner 与 IKCONFIG：Android 15、6.6.89-android15-8、AArch64、4K、header v4、启用 KPROBES/MODULES/MODVERSIONS/SELinux。保存完整配置和镜像哈希。
3. 发现随包 vmlinux 的 banner/config 哈希与 boot 不同；构建以固定上游的 android15-6.6 DDK 路径为候选，不能假定随包 vmlinux 完全匹配。
4. 获取 KernelSU 与 KernelSU-Next，分别固定 `df03912f70d92ff2aa9762ef82d607033d37e1da`、`8d41fffa5dd791d5f25eb279185342d60a0f42f2`。两者均具有 CONFIG_KSU_DISABLE_MANAGER；首选 KernelSU LKM，不开启 debug/shell 授权，不关闭 policy profiles。
5. 从原始脚本第 84 行开始在内存读取压缩包，验证脚本与两 ELF 哈希：主程序为 Android 动态 AArch64，使用 linker64 和 libandroid/liblog/libm/libdl/libc；helper 为无 PT_INTERP/DT_NEEDED 的 AArch64 ELF。未执行载荷。
6. 保留用户签名 RAR，解包并固定签名源码/密钥哈希。原始工具代码不变，仅通过适配层使用主机 Python 与 OpenSSL。
7. 验证链式 boot、普通 init_boot+vbmeta、无 footer 修补结果的 metadata 准备、错误分区/缺少 vbmeta/输出覆盖拒绝、签名前后与回滚的字节和哈希。
8. 原工具会追加相同属性描述符；首轮严格按个数检查失败。诊断记录在 `verification/signing-diagnostic.log`；现允许内容相同的属性重复，仍检查所有其他描述符和签名。首轮失败命令退出码 1；修正后集成测试全部通过。

## Actions 已完成

运行：https://github.com/NzflpKfnjos/78boot/actions/runs/37955028543

分支 `codex/no-manager-actions`，构建 commit `bed73dcf80ed3a7fabd448231fa076caf73a3bfc`。host-policy、lkm、userspace、signed-candidate 四任务均成功。

下载复核确认：镜像输出哈希与 manifest 一致，init_boot 哈希和 vbmeta RSA4096 签名有效；vbmeta 中 init_boot 描述符与镜像完全一致，其他描述符内容保持；ramdisk 内 init 与 kernelsu.ko 分别匹配下载的 ksuinit 和 LKM；编译命令含 CONFIG_KSU_DISABLE_MANAGER=1，不含 CONFIG_KSU_DEBUG=1，ramdisk 未启用 allow_shell=1。

产物：

- `verification/actions-download/37955028543/signed/init_boot.img`，8,388,608 bytes，SHA-256 `f5c29a24654f17948c43012ef863b1f26b6d5f793a18fda1992abb3dc26c0cb4`。
- `verification/actions-download/37955028543/signed/vbmeta.img`，12,288 bytes，SHA-256 `3a6fcb7d163bd33c4c0838a6ce7eca499747c2bfe2b8142553c80511f81e2ddd`。
- `verification/actions-download/37955028543/lkm/android15-6.6_kernelsu.ko`，SHA-256 `de18c1048dcdda3eb2e9dfef1a61099c2b99aefc559a82a22a510a1b65f289ef`。
- `verification/actions-download/37955028543/userspace/`，ksuinit、Android ksud 与 Linux 主机修补器，哈希和 ELF 架构已核对。

签名行为的同命令对照：BASELINE 为修补后的预签名镜像，AVB 哈希不匹配，退出 1；MODIFIED 为签名镜像，校验成功，退出 0；ROLLBACK 恢复预签名 bytes，哈希与预签名基线相同，AVB 再次拒绝，退出 1。原始固件另行备份，不混同预签名回滚与恢复原厂镜像。

具体兼容性限制：下载模块的 vermagic 为 `6.6.127-4k-g46a034eca005-dirty`，固件内核为 `6.6.89-android15-8`。固定上游 ksuinit 有重设 vermagic/CRC 的加载路径，后续设备完整 `/proc/modules` 中已观察到 kernelsu，确认该候选模块成功加载；尚未确认具体经过的兼容处理分支，也尚未验证目标脚本运行。

## 设备开机与检测详情

用户报告已通过 9008 手动刷入并开机，随后 adb 实测确认 TB322FC、Android 15、`ZUXOS_1.1.11.263_251105_PRC`、`_a` 槽和原内核 `6.6.89-android15-8`。`ro.boot.flash.locked=1`、`ro.boot.verifiedbootstate=green`、`ro.boot.vbmeta.device_state=locked`；全局 SELinux 为 Enforcing，adb 为 UID 2000，CapEff 为 0。

完整 `/proc/modules` 中观察到 `kernelsu 155648 0 - Live ... (O)`，已确认模块加载。首次终端预览被截断，之后从完整证据纠正判断；`/sys/module/kernelsu` 不存在不能据此判定模块未加载。证据为 `verification/device-after-flash.json`、`verification/root-traces-device.json`。

已通过 adb 截图和 UI hierarchy 读取 `com.chunqiunativecheck` 4.6.0 的详情：USB 项为 `adb_enabled=1`；root/模块项命中 `u:r:ksu:s0`、`u:object_r:ksu_file:s0`；policy 项命中 `untrusted_app -> ksu_file read`。上游 `kernel/selinux/rules.c:96` 的 `ksu_allow(db, "domain", KERNEL_SU_FILE, ALL, ALL)` 可解释该规则。类型可识别和过宽文件规则是两个独立问题，收缩文件规则不能直接保证所有 root 检测消失。

`Found property(1)` 当前 UI 未展示属性名与值，不能归因到本次 root 修改。详见 `verification/device-detection-findings.json` 和 `verification/detector-latest.png`。未修改系统属性、SELinux、USB 调试或设备镜像。

## 嵌入脚本的自动启动候选

用户要求完整脚本直接嵌入 boot。该 Android 15 布局的 boot 只放内核，init_boot 放启动 ramdisk，因此实现嵌入 init_boot：完整原脚本内嵌在固定用途 bootstrap 的 .rodata 中，bootstrap 完整内嵌在 LKM 中，LKM 放入 init_boot ramdisk，最后使用指定工具重签 init_boot 和配套 vbmeta。没有 adb 暂存区执行依赖，不修改原始脚本第 84 行之后的二进制字节。

启动流程：init second_stage hook 将签名 LKM 内的固定 bootstrap 写入 /dev/root-control-boot（root-only/500/ksu_file）；post-fs-data oneshot init 服务准备 root 私有目录及固定资产，并启用上游 SELinux 查询隔离；sys.boot_completed=1 后另一个 oneshot 服务启动完整脚本，参数为 1、stdin 为 1 加换行。每个 boot_id 最多一次尝试，保留退出码和 root 私有日志。

授权变更：CONFIG_KSU_DISABLE_MANAGER 构建下，非 root 无法获取 KernelSU FD，不允许 app/shell/保存 allowlist 授予 su，关闭 su compatibility；删除 domain -> ksu_file 全权限，改为仅 init 和可信 root domain。SELinux 查询隔离在 second_stage 开启并由 bootstrap 确认成功，失败不发布 prepared 状态。USB 调试与未知属性保持原值，不能承诺所有检测方式无法识别。

停用自动执行：`adb shell touch /data/local/tmp/root-control.disable`；删除标记会在下次开机重新启用。标记不用于提权，普通 app 无 /data/local/tmp 的创建权限。

主机测试 `verification/autostart-host-tests.json`：未添加服务 BASELINE 无执行；MODIFIED fixture 收到 argument=1/stdin=1，退出 7；ROLLBACK 恢复无服务基线。覆盖本次开机不重复、下次开机重试、标记停用、非 root 和未开机完成拒绝。补丁与回滚见 `verification/autostart-patch-transaction.json`、`scripts/ROLLBACK-autostart.sh`。

新的 Actions：https://github.com/NzflpKfnjos/78boot/actions/runs/37962844664 ，构建 commit `744d5bb5f615afdd212cfbf9ccf5d8d0f26489e2`，四任务成功。下载复核报告 `verification/autostart-artifact-verification.json` 确认 exact_bootstrap_in_module=true、完整原脚本 2,931,586 bytes、external_stage_required=false；init_boot 有效数据 7,483,392 bytes，分区总长仍为 8,388,608 bytes。

新产物哈希：init_boot.img `3888ca88a3e32c2ed142b0a79fb0f02d7bb76933cf010b1047d8c9cd08049669`；vbmeta.img `6227585db6058e3236c2433ca54f92f9589f492cdcdfbcda78a5d75341b1eb0a`；LKM `cb440a14ba3fb6dba2d83306208e0a37f4781c62d757a624a413a7b9853ab92d`；bootstrap `4cac653c9527d6be8786ccda58efa96f3fbd6c9064aeccec401ceab63b2b89fe`。

真实 Android 上仅测试了非 root 调用 bootstrap：UID 2000 被拒绝，退出 1，RootControl 日志为 `root-only preparation required: Operation not permitted`。临时二进制已删除；没有安装服务、修改 device policy 或运行载荷。native 真实函数体测试则验证 root 可获得 driver FD、shell/app UID 被拒绝、非 root app grant 恒 false。新 boot 流程需用户刷入后执行。

## 设备相关未完成项

已完成开机与模块加载观察，尚无可信 root 上下文中的 UID/capabilities/SELinux 及真实载荷运行记录。仍需建立 `/data/adb/ksud` 与固定脚本的首次部署路径，并收缩面向普通 app 的不必要文件授权。设备完整 root 流程未验收。

计划阶段 1 的原始脚本设备基线尚未完成；完整 `check/run/status/logs`、SELinux 调整、开机启动、升级卸载依赖该基线。现有 `root-control 1|2` 与安装入口保留。

## 固定的四个验收角色

- MODIFIED_FILE：`/Users/a77/Desktop/git/78boot/bin/root-control`
- DIFF_FILE：`/Users/a77/Desktop/git/78boot/verification/root-control.patch`
- VERIFICATION：`/Users/a77/Desktop/git/78boot/verification/VERIFICATION.txt`
- ROLLBACK：`/Users/a77/Desktop/git/78boot/scripts/ROLLBACK.sh`

本次主机复验观察：BASELINE 为入口不存在，shell 退出 127（macOS）；MODIFIED 执行无害 fixture，stdout 为 `fixture mode=1 secret=unset`，退出 7；ROLLBACK 删除入口副本，恢复不存在状态，退出 127。Android UID/root 所有权为明确的主机模拟。这四个角色继续复用；签名证据与 Actions 记录是补充。
