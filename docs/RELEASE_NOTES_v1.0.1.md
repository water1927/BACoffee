# BACoffee v1.0.1

BACoffee v1.0.1 是面向 Windows x64 与 MuMu 模拟器的首个公开正式版。BACoffee 基于 BAAS、BASH 相关代码与工作流程进行二次开发，并完成了功能改进、流程升级和 UI 美化。本项目为非官方、非营利项目。

## 下载与使用

普通用户请下载 `BACoffee_v1.0.1_Windows_x64.zip`，核对 `SHA256SUMS.txt` 后完整解压，再双击 `BACoffee.exe`。不要下载 GitHub 自动生成的 Source code ZIP 代替发行包。

发行包已携带便携 Python、BAAS、OCR 和必要依赖。BACoffee SAT 与主程序共享运行环境，用户不需要安装 Python，也不要单独移动 `BACoffee SAT.exe`。

## 主要内容

- 提供咖啡厅流程、学生邀请、摸头、定时执行及 Windows 唤醒/休眠管理；
- 内置 202 张学生头像，并支持通过 BACoffee SAT 导入外置头像；
- 邀请优先顺序及模拟器相关设置支持自动保存；
- 支持官服、B服、MuMu 实例及 Android 版本配置；
- 包含托盘功能，便于暂时关闭窗口；
- MuMu ADB 端口查询、启动后重新确认及连接验证；
- BACoffee SAT 与主程序共享便携图像处理运行环境。

## 重要说明

本版本未使用受信任的商业代码签名证书，Windows 可能显示“未知发布者”。请从项目的 GitHub Release 下载并核对 SHA-256。

自动化效果仍可能受游戏版本、MuMu/Android 版本、Windows 权限、网络和安全软件影响。首次使用时请在电脑旁完成一次完整流程验证。

使用前请阅读压缩包内的 `README.md`、`DISCLAIMER.md` 和 `SAT_使用前请阅读.md`。
