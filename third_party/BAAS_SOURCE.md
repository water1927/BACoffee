# BAAS source reference

- Project: Blue Archive Auto Script (BAAS)
- Upstream: <https://gitee.com/pur1fy/blue_archive_auto_script.git>
- Release baseline commit: `5be8600f7f47008023046a51a5c084fb67e23b83`
- License: GPL-3.0-only; see `BAAS_LICENSE`.

BACoffee Windows Release 包在 `third_party/BAAS` 中随附本版本实际使用的 BAAS Python 源码、配置和运行资源。普通 Git 仓库不重复提交体积较大的 OCR 模型、设备组件和图片运行资源。

BACoffee 对 BAAS 的调用入口、配置生成、运行环境定位和任务编排位于 `src/bacoffee/BACoffee.py`。

BACoffee 使用的 BAAS 修改文件：

- `core/picture.py`：游戏更新提示处理。补丁为 `BAAS-game-update-match.patch`。
- `module/cafe_reward.py`：跨咖啡厅与换装邀请确认。补丁为 `BAAS-invitation-confirmation.patch`。

Windows Release 同时随附实际使用的完整修改后 Python 文件。补丁适用于下列文件指纹对应的源码。构建所需完整资源由同版本 Windows 发行包提供。

- `core/picture.py`：补丁前 SHA-256 `5C79885EFF897099CA4CD41388B0E352849B6EDA80F90B08E173721980FE6650`；本版修改后 `7F3F3AE2010D3373F722B41D3CA8FE03D5479E71D426685681C1EF86A45E6090`。
- `module/cafe_reward.py`：补丁前 SHA-256 `53136782B50B87340D974396E64A9722C257109109122A9E7E82D21B28C0AF28`；本版修改后 `D76875A62410B047F247433FC300EC273F23C08E3C629760377F3943DBB5C743`。

- `core/Baas_thread.py`：截图尺寸与 OCR 兼容处理。补丁 `BAAS-screenshot-ocr-sync.patch`；补丁前 SHA-256 `FF10272C25393D5F76D5F6DD346FD2CCFEAD85B844B376B1FBDB4C7CA3403F9D`，本版修改后 `7395BA72C61ED3486DFB638A7F143D04A89E6D71B42CC98DB46494FC6C565A7A`。
