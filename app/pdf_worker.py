"""Disposable, resource-limited PDF parser. No server configuration or credentials."""
import io
import json
import os
import sys


def limits():
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU,(20,20))
        # RLIMIT_DATA is supported on macOS and Linux; AS is additionally useful on Linux.
        cap=512*1024*1024
        if sys.platform.startswith('linux'):
            resource.setrlimit(resource.RLIMIT_DATA,(cap,cap))
            resource.setrlimit(resource.RLIMIT_AS,(cap,cap))
        resource.setrlimit(resource.RLIMIT_FSIZE,(64*1024*1024,64*1024*1024))
    except (ImportError,ValueError,OSError):
        raise RuntimeError('Bu platformda PDF kaynak sınırları uygulanamadı') from None


def extract(raw):
    from pypdf import PdfReader
    reader=PdfReader(io.BytesIO(raw))
    if reader.is_encrypted or len(reader.pages)>1000:raise ValueError('PDF şifreli veya sayfa sınırı aşıldı')
    pieces=[];size=0;has_text=False
    for index,page in enumerate(reader.pages):
        text=page.extract_text() or '';has_text|=bool(text.strip())
        piece=f'[Sayfa {index+1}]\n'+text;size+=len(piece.encode('utf-8'))
        if size>32*1024*1024:raise ValueError('PDF metni çok büyük; dosyayı bölerek yükle')
        pieces.append(piece)
    if not has_text:raise ValueError('PDF metin içermiyor; taranmış sayfaları görsel olarak yükle')
    return '\n'.join(pieces)


def main():
    try:
        limits();result={'text':extract(sys.stdin.buffer.read(32*1024*1024+1))}
    except BaseException:
        result={'error':'PDF okunamadı veya güvenli işleme sınırını aştı; dosyayı bölerek tekrar dene'}
    sys.stdout.write(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()
