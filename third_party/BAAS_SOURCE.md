# BAAS source reference

- Project: Blue Archive Auto Script (BAAS)
- Upstream: <https://gitee.com/pur1fy/blue_archive_auto_script.git>
- Release baseline commit: `5be8600f7f47008023046a51a5c084fb67e23b83`
- License: GPL-3.0-only; see `BAAS_LICENSE`.

BACoffee v1.0.1 的 Windows Release 包在 `third_party/BAAS` 中随附本版本实际使用的 BAAS Python 源码、配置和运行资源。普通 Git 仓库不重复提交体积较大的 OCR 模型、设备组件和图片运行资源。

BACoffee 对 BAAS 的调用入口、配置生成、运行环境定位和任务编排位于 `src/bacoffee/BACoffee.py`。如果今后直接修改 BAAS 上游文件，应在本目录加入相对于上述固定提交的补丁，并同步更新 `THIRD_PARTY_NOTICES.md`。
