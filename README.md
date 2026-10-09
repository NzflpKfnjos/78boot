# 固定脚本 root 执行入口

当前实现是一个 Android shell 执行入口，不会授予 root，也没有修改 KernelSU/KernelSU-Next 的内核授权逻辑。

## 已实现的限制

- 仅接受 UID 0 调用；普通应用、普通 adb shell 直接调用会被拒绝。
- 只执行 `/data/adb/root-control/scripts/1_no_login.sh`，只接受一个参数 `1` 或 `2`。
- 校验完整脚本 SHA-256：`e9cb5f84428910e83c0c43957fa8deb01ce4fc3d7b899aaecea7810993c62200`。
- 检查安装目录和文件的所有者、权限及符号链接；目录要求 root:root/700，脚本要求 root:root/400，入口要求 root:root/500。
- 安装时先复制到私有暂存目录，再验证脚本与入口哈希，最后发布；拒绝覆盖已有安装。
- 执行脚本前清除继承的环境变量，通过 stderr 输出启动或拒绝原因，并保留脚本退出码。

这里的“仅自己”具体落实为“仅可信 root 启动上下文”。尚未实现对某个人、应用签名或专用非 root UID 的认证。UID 0 的其他进程也会通过身份检查，不能把 root-only 理解为独占 root。

拥有通用 `su` 或已经获得 root 的进程，仍然可以修改这些文件或绕过入口。已获取并固定两个上游源码，首个构建候选采用 KernelSU 的 `CONFIG_KSU_DISABLE_MANAGER=y`、非 debug 配置及默认关闭的 shell 授权；实际内核隔离、首次部署与启动上下文仍需设备验证。固件分析见 `verification/stage0/baseline.json`，源码锁定见 `config/upstreams.lock.json`。

执行入口没有提供隐藏文件、隐藏进程或绕过检测功能，也无法保证 root 不被发现。它对脚本本身不做沙箱隔离；通过校验的脚本及内嵌 ELF 会持有其 root 上下文的权限。

## 主机验证

```sh
python3 tests/test_root_control.py
```

测试使用无害 fixture 替代真实脚本，显式模拟 Android UID 与 root 文件所有权。覆盖两个合法模式、未授权身份、参数注入、篡改、错误权限、符号链接、缺失文件、安装验证以及新增入口的回滚。

`verification/VERIFICATION.txt` 保存实际 stdout、stderr、退出码和生产文件哈希。这些是主机策略结果，不是 Android 启动、真实 SELinux 或原始 payload 的运行结果。

`verification/root-control.patch` 是从无入口文件到新增入口的补丁。`scripts/ROLLBACK.sh` 是离线审阅工具：传入一个入口副本的绝对路径后，将其删除以恢复“入口不存在”的基线。不要把它当作内核撤权或完整设备卸载工具。

## Android 部署

前提：设备已提供可信 root 上下文，`/system/bin/toybox` 支持所用命令，`/data/adb` 已存在且为 root:root/700。不要直接改变不符合要求的已有系统目录权限；先确认设备与所选上游的目录约定。

先从主机推送文件：

```sh
adb push bin/root-control /data/local/tmp/root-control
adb push scripts/install.sh /data/local/tmp/install-root-control.sh
adb push /Users/a77/Desktop/git/tomato/release/1_no_login.sh /data/local/tmp/1_no_login.sh
```

在已获得可信 root 的设备 shell 中安装：

```sh
/system/bin/sh /data/local/tmp/install-root-control.sh \
    /data/local/tmp/1_no_login.sh /data/local/tmp/root-control
```

由可信 root 启动服务或 root shell 执行：

```sh
/data/adb/root-control/bin/root-control 1
/data/adb/root-control/bin/root-control 2
```

此处不提供普通 adb shell 自动获取 root 的命令。也没有自动创建开机服务，避免未经验证的载荷在开机时运行。

安装脚本本身必须通过可信部署流程传输、审阅和运行。内部哈希可检测载荷变化，不能认证被替换的安装脚本。更新目标脚本时，需要重新审阅并同步修改入口中的脚本哈希及安装脚本中的入口哈希。

## 固件基线与 GitHub Actions

已从本地 TB322 ZUXOS_1.1.11.263 固件确认 Android 15、AArch64、`6.6.89-android15-8`、4K 页和 boot header v4。随包 `vmlinux` 的版本 banner 与 `boot.img` 不同，不能把它直接视为匹配的内核构建基线。完整脚本及两个内嵌 ELF 的哈希已验证，ELF 静态依赖记录在 `verification/stage0/baseline.json`，未运行真实载荷。

`.github/workflows/tb322-root.yml` 在 `codex/no-manager-actions` 分支 push 时运行，也支持手动触发。它按 `config/upstreams.lock.json` 固定 KernelSU commit，构建 `android15-6.6` LKM、Android `ksuinit`/`ksud` 以及主机修补器，用 LKM 修补 `init_boot` 副本，再调用提供的签名工具重建并验证 `init_boot` 与 `vbmeta`。构建记录、模块、userspace 和签名镜像分别作为 Actions artifacts 上传。

签名工具源文件按原字节保存于 `tools/avb/`。两个密钥仅放在 `ROOT_CONTROL_AVB_RSA2048`、`ROOT_CONTROL_AVB_RSA4096` Actions Secrets 中，值为原文件的 Base64；构建时恢复到临时目录并校验固定哈希，结束时删除。不要将私钥添加到 Git。

## 镜像签名

用户提供的工具在 `签名工具/avb/`，原始 RAR 保留。主机适配层仅替换 Python/OpenSSL 启动路径，实际签名仍使用原工具的 `AvbRebuilder` 与 `avbtool.py`。

- 该固件的 `boot` 是 `SHA256_RSA4096` 链式分区，修改后重签 `boot.img`。
- `init_boot` 是 hash-only 分区，修改后必须同时重建配套 `vbmeta.img`。
- 输出只在所有主机校验通过后发布，保存哈希、命令、stdout/stderr、日志与预签名备份。签名有效不能代替设备开机验证。

```sh
python3 scripts/sign_images.py --boot /absolute/path/to/patched-boot.img \
  --output /absolute/path/to/new-signed-boot-directory

python3 scripts/sign_images.py --init-boot /absolute/path/to/patched-init_boot.img \
  --vbmeta /absolute/path/to/matching-vbmeta.img \
  --output /absolute/path/to/new-signed-init-boot-directory
```

以上输入须保留可解析的原 AVB metadata。修补器去掉 footer 或改变载荷长度时，先用 `scripts/prepare_avb_input.py --stock STOCK --patched PATCHED --output INTERMEDIATE` 附加原始 metadata，再把中间文件交给签名入口。中间文件的哈希尚未更新，不能作为最终镜像。

`python3 tests/test_sign_images.py` 用原始固件的副本测试真实签名、篡改拒绝、配套 vbmeta 更新及回滚，证据在 `verification/signing-tests.json`。测试目录需要首次为空。输出中的 `ROLLBACK.sh` 接受一个镜像副本的绝对路径，恢复到签名前的输入；设备恢复应使用匹配系统版本与槽位的原始镜像。

## 下一步设备验证

```sh
python3 scripts/collect_device.py
# 多设备时添加 --serial SERIAL
```

当前没有 adb/fastboot 设备连接，尚未确认运行系统与固件包一致、活动槽位、bootloader 状态或 LKM 可加载性。首先验证签名候选能启动，再完成无需 Manager 的 `/data/adb/ksud` 与固定脚本部署路径，记录 UID、capabilities 和 SELinux domain。随后在同一设备验证原始脚本的两个模式，补齐 `check/run/status/logs`、升级卸载，并在确认可无人值守运行后接入可关闭的开机服务。
