"""Bounded user-local PNG retention for the text UI; never a model-selected path."""
import base64
from copy import deepcopy
from dataclasses import replace
import os
from pathlib import Path
import re
import struct
import uuid


def capture_directory():
    return Path(os.environ.get('LOCALAPPDATA', str(Path.home()/'.cache'))) / 'CODA' / 'captures'


def retain_capture(result):
    if not result.success or not isinstance(result.data,dict):
        return result
    page = result.data.get('structuredContent',{})
    if not isinstance(page,dict) or page.get('kind') != 'browser_page' or not isinstance(page.get('capture'),dict) or page['capture'].get('path'):
        return result
    try:
        block = next(b for b in result.data.get('content',[]) if isinstance(b,dict) and b.get('type')=='image' and b.get('mimeType')=='image/png')
        if len(block['data']) > 1400000:
            raise ValueError('Oversized capture')
        png = base64.b64decode(block['data'],validate=True)
        if not png.startswith(b'\x89PNG\r\n\x1a\n') or len(png)>1048576 or len(png)<24:
            raise ValueError('Invalid capture')
        width,height = struct.unpack('>II',png[16:24])
        if width != 1000 or height != 700:
            raise ValueError('Invalid viewport')
        folder = capture_directory()
        for ancestor in (folder,*folder.parents):
            if ancestor.is_symlink() or (ancestor.exists() and getattr(ancestor.lstat(),'st_file_attributes',0)&0x400):
                raise OSError('Capture directory cannot traverse links')
        folder.mkdir(parents=True,exist_ok=True,mode=0o700)
        files = sorted((p for p in folder.iterdir() if re.fullmatch(r'coda-[a-f0-9]{32}\.png',p.name) and not p.is_symlink()),key=lambda p:p.stat().st_mtime)
        for old in files[:-9] if len(files)>=10 else []:
            old.unlink()
        path = folder / ('coda-'+uuid.uuid4().hex+'.png')
        descriptor = os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(descriptor,'wb') as file:
            file.write(png)
        data = deepcopy(result.data)
        data['structuredContent']['capture'].update(path=str(path),storage='user_local_bounded_cache')
        return replace(result,data=data)
    except (OSError,ValueError,KeyError,StopIteration,TypeError):
        data = deepcopy(result.data)
        data['structuredContent']['capture']['save_failed'] = True
        return replace(result,data=data)
