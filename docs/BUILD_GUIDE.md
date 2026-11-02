# 构建 BACoffee v1.1.0

## 环境要求

- Windows x64。
- Python 3.9.13。
- `build-requirements.lock` 中列出的构建依赖。
- 完整解压的同版本 Windows 发行包，用于提供运行资源。

## 构建命令

```powershell
python scripts/build_release.py --release-source "已解压的发行目录" --build-root "构建目录" --output-dir "输出目录" --version v1.1.0
```

`--release-source` 需要包含 `runtime`、`third_party`、`assets` 和 SAT 启动器。BAAS 文件指纹与补丁说明见 [BAAS_SOURCE.md](../third_party/BAAS_SOURCE.md)。

输出包括 Windows ZIP、逐文件清单和 `SHA256SUMS.txt`。用户设置、用户头像和运行日志不属于构建输入。

修改 `build/sat_launcher.c` 后，需要使用 Windows C 编译器重新编译 SAT 启动器并更新对应来源元数据。仅修改 BAC 或 SAT 的 Python 源码时，无需重新编译该启动器。
