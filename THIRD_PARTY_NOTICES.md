# Third-party notices

## BAAS

- Project: Blue Archive Auto Script (BAAS)
- Repository: `https://gitee.com/pur1fy/blue_archive_auto_script.git`
- Pinned commit used by the current release: `5be8600f7f47008023046a51a5c084fb67e23b83`
- License: GPL-3.0-only; the corresponding license text is included with the release.
- Usage and changes: BACoffee vendors a release-specific BAAS worker and adapts its configuration, runtime loading, OCR integration and task orchestration for the BACoffee workflow. The public repository does not vendor the full runtime copy; the binary Release ZIP carries the reviewed runtime and BAAS files.

## BASH

BACoffee is described as a secondary development based on BAAS and BASH-related code and workflows, with functional improvements, workflow integration and UI redesign. This Release does not ship `BASH.exe` or a separately installed BASH distribution. The coffee-invitation orchestration in this package is implemented in BACoffee around the vendored BAAS worker.

No BASH upstream repository, version, copyright notice or license is invented or asserted without verified evidence. Any BASH-origin material remains subject to the rights and license terms of its original author or rights holder.

## Other assets

- Student avatar URLs: Kivo Wiki, `https://kivo.wiki/` and `https://static.kivo.wiki/`.
- Comfortaa font: SIL Open Font License 1.1; the license text is bundled with the release.
- Python runtime distributions: exact staged versions and local metadata are recorded in `runtime-requirements.lock` and `runtime-sbom.md` inside the Release ZIP.

Names of third-party projects and games remain the property of their respective owners. BACoffee is not an official product of any project, game publisher, platform or emulator vendor named above.

BACoffee is a non-profit project. If a rights holder believes that included material infringes their lawful rights, they may contact the maintainers through the repository Issues. After verification, the maintainers will correct attribution or licensing information, replace the material, or remove it as appropriate. This notice does not override any applicable license or legal obligation.
