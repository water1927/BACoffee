# Third-party notices

## BAAS

- Project: Blue Archive Auto Script (BAAS)
- Repository: `https://gitee.com/pur1fy/blue_archive_auto_script.git`
- Pinned commit used by the current release: `5be8600f7f47008023046a51a5c084fb67e23b83`
- License: GPL-3.0-only; the corresponding license text is included with the release.
- Usage and changes: BACoffee vendors a release-specific BAAS worker and adapts its configuration, runtime loading, OCR integration and task orchestration for the BACoffee workflow. The public repository does not vendor the full runtime copy; the binary Release ZIP includes the runtime and BAAS files.
- Modified BAAS files support game-update prompts, invitation confirmation and screenshot handling. The Release ZIP includes the complete modified Python files. Corresponding source references and patches are listed in `third_party/BAAS_SOURCE.md`.

## BASH

- Project: BASH.
- Project source page: <https://www.bilibili.com/video/BV1both6kE65/>.
- Usage and changes: BACoffee references BASH-related student portrait matching and invitation workflows, integrated into BACoffee's invitation implementation. Cafe interaction actions and invitation confirmation use the vendored BAAS worker. This Release does not ship a separate BASH executable.
- Attribution: Credit for BASH-origin code, resources and workflows belongs to the original BASH authors. Such material remains subject to its original license and rights holder terms.

## Other assets

- Student avatar URLs: Kivo Wiki, `https://kivo.wiki/` and `https://static.kivo.wiki/`.
- Comfortaa font: SIL Open Font License 1.1; the license text is bundled with the release.
- Python runtime distributions: exact staged versions and local metadata are recorded in `runtime-requirements.lock` and `runtime-sbom.md` inside the Release ZIP.

Names of third-party projects and games remain the property of their respective owners. BACoffee is not an official product of any project, game publisher, platform or emulator vendor named above.

BACoffee is a non-profit project. If a rights holder believes that included material infringes their lawful rights, they may contact the maintainers through the repository Issues. After verification, the maintainers will correct attribution or licensing information, replace the material, or remove it as appropriate. This notice does not override any applicable license or legal obligation.
