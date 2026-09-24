import argparse
import hashlib
import struct
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit
from urllib.request import urlopen

from Crypto.Cipher import AES

VERSION = '0.0.2'
SERVERS = {
    'tw': ('TW/HK/MO', 'https://l14-prod-hk-patch-sirius.gamerfusiontech.com/prod/hk_27f3c91e8b62d6056c7a19f2e83b6d10'),
    'en': ('EN REGION', 'https://l14-prod-sg-patch-sirius.bilibiligame.net/prod/en_3e8a72c5f1d9066b9a37c2e85f619db0'),
    'kr': ('KOREA', 'https://l14-prod-sg-patch-sirius.bilibiligame.net/prod/kr_461b4e9a7c2385f0e2d966a1b73c8f52'),
}
LANGUAGES = {'zh-Hans': '简体中文', 'zh-Hant': '繁体中文', 'en': 'English', 'ko': '한국어', 'ja': '日本語'}
CATALOGS = Path(__file__).parent / 'downloads/catalog'


def decrypt(data, filename):
    if data.startswith(b'UnityFS\0'):
        return data
    key = bytes.fromhex('7372a4ee777db361ad896c99e408a182')
    seed = bytes.fromhex('ee24a70238e2a0e5')
    nonce = hashlib.sha256(seed + filename.encode('utf-8')).digest()[:8]
    aes = AES.new(key, AES.MODE_CTR, nonce=nonce, initial_value=0)
    return aes.decrypt(data[:16384]) + data[16384:]


def parse(data):
    def u32(offset):
        return struct.unpack_from('<I', data, offset)[0]

    def array(offset):
        if offset == 0xFFFFFFFF:
            return []
        return struct.unpack_from(f'<{u32(offset - 4) // 4}I', data, offset)

    def text(offset):
        if offset == 0xFFFFFFFF:
            return ''
        if offset & 0x40000000:
            parts = []
            while offset != 0xFFFFFFFF:
                part, offset = struct.unpack_from('<II', data, offset & 0x3FFFFFFF)
                parts.append(text(part))
            return '/'.join(reversed(parts))
        pos = offset & 0x3FFFFFFF
        return data[pos:pos + u32(pos - 4)].decode('utf-16-le' if offset & 0x80000000 else 'ascii')

    magic, version, keys = struct.unpack_from('<III', data)
    if magic != 0x0DE38942 or version != 2:
        raise ValueError('不支持的 Catalog 格式')
    locations = set()
    for pos in range(keys, keys + u32(keys - 4), 8):
        locations.update(array(u32(pos + 4)))
    result = []
    for pos in sorted(locations):
        primary, internal, _, deps = struct.unpack_from('<4I', data, pos)
        result.append({'offset': pos, 'primary_key': text(primary),
                       'internal_id': text(internal), 'dependencies': array(deps)})
    return result


def browse(entries):
    bundles = {e['offset']: e for e in entries
               if e['internal_id'].startswith('https://dummy.net/')
               and e['internal_id'].endswith('.bundle')}
    files = {}
    for entry in entries:
        if entry['internal_id'].startswith('Assets/'):
            ids = [i for i in entry['dependencies'] if i in bundles]
            if ids:
                files.setdefault(entry['primary_key'], set()).update(ids)
    for ident, entry in bundles.items():
        files['bundles/' + entry['primary_key']] = {ident}
    return bundles, files


class Handler(BaseHTTPRequestHandler):
    def listing(self, label, rows):
        title = f'nnnotes v{VERSION} by MetaMiku /' + label
        data = ('<!doctype html><meta charset="utf-8"><title>' + escape(title)
                + '</title><h1>' + escape(title) + '</h1><hr><ul>'
                + '\n'.join(sorted(rows)) + '</ul><hr>').encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = unquote(urlsplit(self.path).path)
        parts = path.strip('/').split('/') if path.strip('/') else []
        if not parts:
            self.listing('', [f'<li><a href="/{key}/">{name}/</a></li>' for key, (name, _) in SERVERS.items()])
            return
        region = parts[0]
        if region not in SERVERS:
            self.send_error(404)
            return
        if len(parts) == 1:
            self.listing(SERVERS[region][0] + '/', ['<li><a href="/">../</a></li>'] +
                         [f'<li><a href="/{region}/{key}/">{name}/</a></li>' for key, name in LANGUAGES.items()])
            return
        language = parts[1]
        if language not in LANGUAGES:
            self.send_error(404)
            return
        cdn = SERVERS[region][1]
        key = (region, language)
        if key not in self.server.catalogs:
            catalog = CATALOGS / region / f'catalog_main_{language}.bin'
            if not catalog.exists():
                with urlopen(cdn + f'/asset/Android/{catalog.name}', timeout=60) as response:
                    raw = response.read()
                catalog.parent.mkdir(parents=True, exist_ok=True)
                catalog.write_bytes(raw)
            self.server.catalogs[key] = browse(parse(catalog.read_bytes()))
        bundles, files = self.server.catalogs[key]
        base = f'/{region}/{language}/'
        if len(parts) > 2 and parts[2] == 'download':
            ident = parts[3] if len(parts) == 4 else ''
            if not ident.isdecimal() or int(ident) not in bundles:
                self.send_error(404)
                return
            entry = bundles[int(ident)]
            name = entry['internal_id'].rsplit('/', 1)[1]
            url = cdn + entry['internal_id'].removeprefix('https://dummy.net')
            with urlopen(url, timeout=60) as response:
                data = decrypt(response.read(), name)
            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + quote(name))
        else:
            prefix = '/'.join(parts[2:])
            prefix += '/' if prefix else ''
            parent = base + prefix.rstrip('/').rsplit('/', 1)[0] + '/' if '/' in prefix.rstrip('/') else base
            if not prefix:
                parent = f'/{region}/'
            rows = {f'<li><a href="{quote(parent)}">../</a></li>'}
            for name, ids in files.items():
                if not name.startswith(prefix):
                    continue
                tail = name[len(prefix):]
                if '/' in tail:
                    directory = prefix + tail.split('/')[0] + '/'
                    rows.add(f'<li><a href="{base}{quote(directory)}">{escape(tail.split("/")[0])}/</a></li>')
                else:
                    for ident in sorted(ids):
                        label = tail if len(ids) == 1 else tail + ' — ' + bundles[ident]['primary_key']
                        rows.add(f'<li><a href="{base}download/{ident}">{escape(label)}</a></li>')
            self.listing(SERVERS[region][0] + '/' + LANGUAGES[language] + '/' + prefix, rows)
            return
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', action='version', version=f'nnnotes {VERSION}')
    parser.add_argument('port', nargs='?', type=int, default=8000)
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.catalogs = {}
    print(f'nnnotes: http://127.0.0.1:{args.port}', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
