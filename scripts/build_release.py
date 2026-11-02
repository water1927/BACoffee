"""Build the shared host and a clean portable release, without user data."""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]


def digest(value):
    if isinstance(value, Path):
        h = hashlib.sha256()
        with value.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                h.update(block)
        return h.hexdigest().upper()
    return hashlib.sha256(value).hexdigest().upper()


def encode(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def content(value):
    return value.read_bytes() if isinstance(value, Path) else value


def excluded(name):
    path = Path(name)
    return (path.parts[0] in ('data', 'user_assets') or '__pycache__' in path.parts
            or path.suffix.lower() in ('.pyc', '.pyo') or path.name == 'release-manifest.json')


def build(release_source, build_root, output_dir, version):
    source, work, output = release_source.resolve(), build_root.resolve(), output_dir.resolve()
    if not re.fullmatch(r'v\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?', version):
        raise ValueError('Expected a version such as v1.1.0')
    if work.exists():
        raise FileExistsError('Use a new build work directory')
    output.mkdir(parents=True, exist_ok=True)
    archive_path = output / ('BACoffee_' + version + '_Windows_x64.zip')
    if archive_path.exists():
        raise FileExistsError('Refusing to overwrite an existing archive')
    # Reuse only a verified native launcher matching its supplied C source.
    old_sat = read_json(source / 'sat-build-metadata.json')
    launcher = source / 'BACoffee SAT.exe'
    launcher_source = REPOSITORY / 'build/sat_launcher.c'
    if (old_sat.get('architecture') != 'shared_gui_host_native_launcher'
            or old_sat['exe']['sha256'] != digest(launcher)
            or old_sat['launcher_source']['sha256'] != digest(launcher_source)):
        raise ValueError('A verified matching native SAT launcher is required')
    if digest(source/'third_party/BAAS/core/picture.py') != '7F3F3AE2010D3373F722B41D3CA8FE03D5479E71D426685681C1EF86A45E6090':
        raise ValueError('This source requires the matching BAAS game-update patch; use the complete current release resource directory')
    if digest(source/'third_party/BAAS/module/cafe_reward.py') != 'D76875A62410B047F247433FC300EC273F23C08E3C629760377F3943DBB5C743':
        raise ValueError('Matching BAAS invitation confirmation resource is required')
    if digest(source/'third_party/BAAS/core/Baas_thread.py') != '7395BA72C61ED3486DFB638A7F143D04A89E6D71B42CC98DB46494FC6C565A7A':
        raise ValueError('Matching BAAS screenshot/OCR synchronization resource is required')
    payload = {}
    for entry in read_json(source / 'release-manifest.json')['files']:
        name = entry['path'].replace('\\', '/')
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Invalid resource manifest path')
        if excluded(name):
            continue
        path = source / relative
        if path.stat().st_size != entry['size'] or digest(path) != entry['sha256'].upper():
            raise ValueError('Resource hash mismatch: ' + name)
        payload[name] = path
    work.mkdir(parents=True)
    stamp = datetime.now(timezone.utc)
    sat = dict(schema=2, version=version, built_at_utc=stamp.isoformat(),
               architecture='shared_gui_host_native_launcher', host='BACoffee.exe', mode='--sat', sources={})
    for name in ('avatar_import_core.py', 'user_avatar_import_tool.py', 'user_tool_core.py'):
        path = REPOSITORY / 'src/sat' / name
        sat['sources'][name] = dict(authoritative='src/sat/' + name,
                                    candidate='sat_source/' + name, sha256=digest(path))
        payload['sat_source/' + name] = path
    spec = REPOSITORY / 'build/BACoffee.spec'
    main_source = REPOSITORY / 'src/bacoffee/BACoffee.py'
    sat.update(exe=dict(path='BACoffee SAT.exe', sha256=digest(launcher)),
               launcher_source=dict(path='build/sat_launcher.c', sha256=digest(launcher_source)),
               spec='build/BACoffee.spec', spec_sha256=digest(spec), resource_root='assets/sat',
               brand_font='assets/fonts/Comfortaa-Light.ttf', image_runtime='runtime/Lib/site-packages', bundled_simhei=False)
    sat_bytes = encode(sat)
    metadata = dict(schema=2, version=version,
                    build_id='BACoffee-' + version + '-shared-' + stamp.strftime('%Y%m%dT%H%M%SZ'),
                    built_at_utc=stamp.isoformat(), source_sha256=digest(main_source), spec_sha256=digest(spec),
                    python='Python ' + sys.version.split()[0], source_relative_path='src/bacoffee/BACoffee.py',
                    schedule_script_sha256=digest(REPOSITORY/'scripts/Install-WakeTask.ps1'),
                    sat_metadata_sha256=digest(sat_bytes), architecture='shared_gui_host_native_launcher',
                    shared_assets='assets', external_image_runtime=True)
    metadata_bytes = encode(metadata)
    (work/'build-metadata.json').write_bytes(metadata_bytes)
    (work/'sat-build-metadata.json').write_bytes(sat_bytes)
    environment = dict(os.environ, BACOFFEE_RELEASE_ROOT=str(source),
                       BACOFFEE_BUILD_METADATA=str(work/'build-metadata.json'),
                       BACOFFEE_SAT_BUILD_METADATA=str(work/'sat-build-metadata.json'), PYTHONDONTWRITEBYTECODE='1')
    with (work/'build.log').open('w', encoding='utf-8') as log:
        subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
                        '--distpath', str(work/'dist'), '--workpath', str(work/'work'), str(spec)],
                       cwd=REPOSITORY, env=environment, stdout=log, stderr=subprocess.STDOUT, check=True)
    payload.update({'BACoffee.exe':work/'dist/BACoffee.exe', 'BACoffee.py':main_source,
                    'BACoffee SAT.exe':launcher, 'build-metadata.json':metadata_bytes,
                    'sat-build-metadata.json':sat_bytes})
    for name in ('README.md', 'LICENSE', 'DISCLAIMER.md', 'PRIVACY.md', 'SECURITY.md',
                 'THIRD_PARTY_NOTICES.md', 'runtime-requirements.lock', 'runtime-sbom.md'):
        payload[name] = REPOSITORY/name
    payload.update({'Install-WakeTask.ps1':REPOSITORY/'scripts/Install-WakeTask.ps1',
                    'Remove-WakeTask.ps1':REPOSITORY/'scripts/Remove-WakeTask.ps1',
                    'third_party/BAAS_SOURCE.md':REPOSITORY/'third_party/BAAS_SOURCE.md',
                    'third_party/BAAS-game-update-match.patch':REPOSITORY/'third_party/BAAS-game-update-match.patch',
                    'third_party/BAAS-invitation-confirmation.patch':REPOSITORY/'third_party/BAAS-invitation-confirmation.patch',
                    'third_party/BAAS-screenshot-ocr-sync.patch':REPOSITORY/'third_party/BAAS-screenshot-ocr-sync.patch',
                    'RELEASE_NOTES.md':REPOSITORY/'docs'/('RELEASE_NOTES_'+version+'.md'),
                    'CHANGELOG.md':REPOSITORY/'CHANGELOG.md',
                    '版本说明.md':REPOSITORY/'docs'/('版本说明_'+version+'.md'),
                    'SAT_使用前请阅读.md':REPOSITORY/'docs/SAT_使用前请阅读.md'})
    config = read_json(source/'third_party/BAAS/config/cn/config.json')
    config['name'] = 'BACoffee portable template'
    payload['third_party/BAAS/config/cn/config.json'] = encode(config)
    note = source/'user_assets/students/README.txt'
    if note.is_file():
        payload['user_assets/students/README.txt'] = note
    entries = []
    for name, value in sorted(payload.items(), key=lambda item:item[0].casefold()):
        if name.startswith(('data/', 'user_assets/')):
            continue
        data = content(value)
        entries.append(dict(path=name, size=len(data), sha256=digest(data)))
    manifest = dict(schema=2, kind='release', version=version, build_id=metadata['build_id'],
                    built_at_utc=metadata['built_at_utc'], source_sha256=metadata['source_sha256'],
                    candidate_source_sha256=metadata['source_sha256'], spec_sha256=metadata['spec_sha256'],
                    architecture='shared_gui_host_native_launcher', immutable_file_count=len(entries),
                    mutable_roots_excluded=['data/', 'user_assets/'], files=entries,
                    exe=dict(path='BACoffee.exe', size=payload['BACoffee.exe'].stat().st_size, sha256=digest(payload['BACoffee.exe'])),
                    sat_exe=dict(path='BACoffee SAT.exe', size=launcher.stat().st_size, sha256=digest(launcher)), sat=sat)
    manifest_bytes = encode(manifest)
    payload['release-manifest.json'] = manifest_bytes
    prefix = 'BACoffee_' + version + '_Windows_x64/'
    with zipfile.ZipFile(archive_path, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr(prefix+'data/', b'')
        for name, value in sorted(payload.items()):
            archive.writestr(prefix+name, content(value))
    with zipfile.ZipFile(archive_path) as archive:
        if archive.testzip() is not None:
            raise ValueError('ZIP CRC verification failed')
        for entry in entries:
            data = archive.read(prefix+entry['path'])
            if len(data)!=entry['size'] or digest(data)!=entry['sha256']:
                raise ValueError('Archived content mismatch: '+entry['path'])
    (output/'release-manifest.json').write_bytes(manifest_bytes)
    note_name = 'RELEASE_NOTES_'+version+'.md'
    (output/note_name).write_bytes((REPOSITORY/'docs'/note_name).read_bytes())
    sums = [digest(output/name)+'  '+name for name in (archive_path.name,'release-manifest.json',note_name)]
    (output/'SHA256SUMS.txt').write_bytes(('\n'.join(sums)+'\n').encode('utf-8'))
    return dict(version=version, archive=str(archive_path), archive_sha256=digest(archive_path),
                archive_bytes=archive_path.stat().st_size, payload_files=len(payload), immutable_files=len(entries),
                source_sha256=digest(main_source), exe_sha256=digest(payload['BACoffee.exe']))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--release-source', type=Path, required=True)
    parser.add_argument('--build-root', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--version', default='v1.1.0')
    args = parser.parse_args()
    print(json.dumps(build(args.release_source,args.build_root,args.output_dir,args.version), ensure_ascii=False, indent=2))
