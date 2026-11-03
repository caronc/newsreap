# Test the MIME codec (attachments, base64 and quoted-printable)
#
# Copyright (C) 2026 Chris Caron <lead2gold@gmail.com>
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.

import gevent.monkey
gevent.monkey.patch_all()

import base64
import binascii
from io import BytesIO

from click.testing import CliRunner

from newsreap.NNTPAsciiContent import NNTPAsciiContent
from newsreap.NNTPBinaryContent import NNTPBinaryContent
from newsreap.NNTPContent import NNTPContent
from newsreap.codecs.CodecHeader import CodecHeader
from newsreap.codecs.CodecMime import CodecMime
from newsreap.codecs.CodecUU import CodecUU
from newsreap.codecs.CodecYenc import CodecYenc

from tests.FakeNNTPServer import FakeNNTPServer
from tests.conftest import config_for
from tests.conftest import connect_to

GROUP = 'alt.binaries.test'

# Some binary data (every byte value)
DATA = bytes(range(256)) * 20


def run_decoders(data, decoders, chunk=None):
    """
    Feeds an article to decoders the way a connection does (`chunk` lines
    at a time when given; a connection only passes whole lines); returns
    (body, contents): the lines no decoder took and what the decoders
    returned
    """
    lines = data.splitlines(keepends=True)
    pieces = [data] if not chunk else [
        b''.join(lines[i:i + chunk]) for i in range(0, len(lines), chunk)]

    buf = BytesIO()
    body = []
    contents = []
    active = None
    for no, piece in enumerate(pieces):
        last = no == len(pieces) - 1
        pos = buf.tell()
        end = buf.seek(0, 2) + buf.write(piece)
        buf.seek(pos)

        while buf.tell() < end:
            if active is None:
                start = buf.tell()
                line = buf.readline()
                active = next(
                    (d for d in decoders if d.detect(line) is not None),
                    None)
                if active is None:
                    body.append(line)
                    continue
                buf.seek(start)

            result = active.decode(buf)
            if result is True:
                if not last:
                    # Wait for more
                    break
                result = active.decoded

            active = None
            if isinstance(result, NNTPContent):
                contents.append(result)

    if active is not None:
        # The article ended inside what a decoder was reading
        result = active.decoded
        if isinstance(result, NNTPContent):
            contents.append(result)

    return b''.join(body), contents


def decoders(tmp_path):
    """
    Returns the decoders a connection uses for an article (header first)
    """
    header = CodecHeader(work_dir=str(tmp_path))
    return [
        header,
        CodecMime(work_dir=str(tmp_path), header=header),
        CodecYenc(work_dir=str(tmp_path)),
        CodecUU(work_dir=str(tmp_path)),
    ]


def b64(data):
    """
    Returns data base64 encoded in lines of 76 characters (CRLF endings)
    """
    return base64.encodebytes(data).replace(b'\n', b'\r\n')


# A message with a text part (quoted-printable, latin-1) and an attachment;
# its boundary is given on a second header line (folded)
MULTIPART = (
    b'From: someone@example.com\r\n'
    b'Subject: A picture\r\n'
    b'MIME-Version: 1.0\r\n'
    b'Content-Type: multipart/mixed;\r\n'
    b'  boundary="==XYZ=="\r\n'
    b'\r\n'
    b'This is a MIME message.\r\n'
    b'--==XYZ==\r\n'
    b'Content-Type: text/plain; charset=iso-8859-1\r\n'
    b'Content-Transfer-Encoding: quoted-printable\r\n'
    b'\r\n'
    b'Caf=E9 is open=\r\n'
    b' today.\r\n'
    b'Second line\r\n'
    b'--==XYZ==\r\n'
    b'Content-Type: application/octet-stream; name="data.bin"\r\n'
    b'Content-Transfer-Encoding: base64\r\n'
    b'Content-Disposition: attachment; filename="data.bin"\r\n'
    b'\r\n' + b64(DATA) +
    b'--==XYZ==--\r\n'
    b'The end.\r\n')


def test_parts_are_decoded_from_a_multipart_article(tmp_path):
    """
    Each part comes back decoded: text (with its character set) and an
    attachment named as posted; the text around the parts stays in the
    body
    """
    body, contents = run_decoders(MULTIPART, decoders(tmp_path))
    header, text, attachment = contents

    assert header['Content-Type'] == \
        'multipart/mixed; boundary="==XYZ=="'

    assert isinstance(text, NNTPAsciiContent)
    assert text.content_type == 'text/plain'
    assert text.charset == 'iso-8859-1'
    assert text.getvalue() == b'Caf\xe9 is open today.\r\nSecond line'
    assert text.filename == ''

    assert isinstance(attachment, NNTPBinaryContent)
    assert attachment.filename == 'data.bin'
    assert attachment.content_type == 'application/octet-stream'
    assert attachment.getvalue() == DATA
    assert attachment.is_valid()

    assert body == b'This is a MIME message.\r\nThe end.\r\n'


def test_articles_arriving_in_small_pieces_decode_the_same(tmp_path):
    """
    What arrives a few lines at a time decodes the same, and a line cut
    in half is put back together
    """
    for chunk in (1, 3, 64):
        body, contents = run_decoders(
            MULTIPART, decoders(tmp_path), chunk=chunk)
        assert [c.getvalue() for c in contents[1:]] == [
            b'Caf\xe9 is open today.\r\nSecond line', DATA]
        assert body == b'This is a MIME message.\r\nThe end.\r\n'

    # A line of base64 arriving in two halves
    codec = CodecMime(work_dir=str(tmp_path), boundary='b')
    encoded = b64(DATA[:57])
    stream = BytesIO(
        b'--b\r\nContent-Transfer-Encoding: base64\r\n'
        b'Content-Type: application/zip\r\n\r\n' + encoded[:30])
    assert codec.decode(stream) is True
    stream = BytesIO(encoded[30:] + b'--b--\r\n')
    assert codec.decode(stream).getvalue() == DATA[:57]


def test_parts_inside_parts_are_decoded(tmp_path):
    """
    A part holding parts of its own (ie: text sent as plain text and HTML)
    is opened up too
    """
    raw = (
        b'Content-Type: multipart/mixed; boundary="outer"\r\n\r\n'
        b'--outer\r\n'
        b'Content-Type: multipart/alternative; boundary="inner"\r\n\r\n'
        b'--inner\r\n'
        b'Content-Type: text/plain\r\n\r\n'
        b'Plain words\r\n'
        b'--inner\r\n'
        b'Content-Type: text/html\r\n\r\n'
        b'<p>Plain words</p>\r\n'
        b'--inner--\r\n'
        b'--outer\r\n'
        b'Content-Type: image/jpeg\r\n'
        b'Content-Transfer-Encoding: base64\r\n\r\n' + b64(DATA[:300]) +
        b'--outer--\r\n')

    body, contents = run_decoders(raw, decoders(tmp_path))
    text, html, image = contents[1:]
    assert text.getvalue() == b'Plain words'
    assert (html.filename, html.getvalue()) == (
        'part2.html', b'<p>Plain words</p>')
    assert (image.filename, image.content_type) == ('part3.jpg', 'image/jpeg')
    assert image.getvalue() == DATA[:300]
    assert body == b''


def test_bodies_encoded_as_a_whole_are_decoded(tmp_path):
    """
    A body that isn't split into parts but is base64, quoted-printable or
    uuencoded as a whole is decoded too
    """
    raw = (
        b'Content-Type: image/png\r\n'
        b'Content-Transfer-Encoding: base64\r\n'
        b'Content-Disposition: inline; filename=photo.png\r\n\r\n' +
        b64(DATA))
    _, contents = run_decoders(raw, decoders(tmp_path))
    assert contents[1].filename == 'photo.png'
    assert contents[1].getvalue() == DATA

    raw = (
        b'Content-Type: text/plain; charset=utf-8\r\n'
        b'Content-Transfer-Encoding: quoted-printable\r\n\r\n'
        b'na=C3=AFve\r\n')
    _, contents = run_decoders(raw, decoders(tmp_path))
    assert contents[1].getvalue().decode(contents[1].charset) == 'naïve'

    uu = binascii.b2a_uu(DATA[:45])
    raw = (
        b'Content-Type: application/octet-stream; name="a.bin"\r\n'
        b'Content-Transfer-Encoding: x-uuencode\r\n\r\n'
        b'begin 644 a.bin\r\n' + uu.replace(b'\n', b'\r\n') +
        b'`\r\nend\r\n')
    _, contents = run_decoders(raw, decoders(tmp_path))
    assert contents[1].getvalue() == DATA[:45]


def test_plain_articles_are_left_alone(tmp_path):
    """
    An article that isn't MIME (or one whose body was fetched on its own,
    so its boundary isn't known) is left as it is
    """
    raw = b'Subject: hi\r\n\r\nJust words\r\n--not a boundary\r\n'
    body, contents = run_decoders(raw, decoders(tmp_path))
    assert body == b'Just words\r\n--not a boundary\r\n'
    assert len(contents) == 1

    # The body alone: nothing to tell where the parts are
    body, contents = run_decoders(
        MULTIPART.split(b'\r\n\r\n', 1)[1],
        [CodecMime(work_dir=str(tmp_path))])
    assert contents == []
    assert b'Caf=E9 is open=' in body


def test_attachment_names_are_read_however_they_are_written(tmp_path):
    """
    Names in other character sets (RFC 2231 and encoded words) are read,
    and a path in a name is never followed
    """
    for header, expected in (
            (b"attachment; filename*=utf-8''caf%C3%A9.jpg", 'café.jpg'),
            (b'attachment; filename="=?utf-8?q?caf=C3=A9.jpg?="',
             'café.jpg'),
            (b'attachment; filename="../../etc/evil.sh"', 'evil.sh'),
            (b'attachment; filename="C:\\\\Windows\\\\evil.exe"',
             'evil.exe')):
        raw = (
            b'Content-Type: multipart/mixed; boundary=b\r\n\r\n--b\r\n'
            b'Content-Type: application/octet-stream\r\n'
            b'Content-Disposition: ' + header + b'\r\n'
            b'Content-Transfer-Encoding: base64\r\n\r\n' + b64(b'x') +
            b'--b--\r\n')
        _, contents = run_decoders(raw, decoders(tmp_path))
        assert contents[1].filename == expected, header


def test_bad_data_never_stops_decoding(tmp_path):
    """
    Broken base64 is left out, a part that never ends is returned as far
    as it got, and a part header that never ends is read as content
    """
    # A line that isn't base64 among good ones
    raw = (
        b'Content-Type: multipart/mixed; boundary=b\r\n\r\n--b\r\n'
        b'Content-Type: application/zip; name=a.zip\r\n'
        b'Content-Transfer-Encoding: base64\r\n\r\n' +
        b64(DATA[:57]) + b'!!!! not base64 ****\r\n' + b64(DATA[57:114]) +
        b'--b--\r\n')
    _, contents = run_decoders(raw, decoders(tmp_path))
    assert contents[1].getvalue() == DATA[:114]

    # Cut off before its last boundary (and without padding)
    raw = (
        b'Content-Type: multipart/mixed; boundary=b\r\n\r\n--b\r\n'
        b'Content-Type: application/zip; name=a.zip\r\n'
        b'Content-Transfer-Encoding: base64\r\n\r\n' + b64(b'hello')[:7])
    _, contents = run_decoders(raw, decoders(tmp_path))
    assert contents[1].getvalue() == b'hello'

    # Nothing but a boundary
    raw = b'Content-Type: multipart/mixed; boundary=b\r\n\r\n--b--\r\n'
    body, contents = run_decoders(raw, decoders(tmp_path))
    assert len(contents) == 1
    assert body == b''

    # A broken boundary parameter: the body is read as it is
    raw = b'Content-Type: multipart/mixed; boundary=\r\n\r\n--b\r\nhi\r\n'
    body, contents = run_decoders(raw, decoders(tmp_path))
    assert body == b'--b\r\nhi\r\n'


def test_a_codec_can_be_used_again_after_a_reset(tmp_path):
    """
    reset() forgets the article (a retry starts over cleanly)
    """
    codecs = decoders(tmp_path)
    run_decoders(MULTIPART[:400], codecs)
    for codec in codecs:
        codec.reset()

    _, contents = run_decoders(MULTIPART, codecs)
    assert contents[2].getvalue() == DATA


def test_files_encoded_as_attachments_decode_back(tmp_path):
    """
    encode() makes a body (and the headers it needs) that decodes back to
    the same file
    """
    source = tmp_path / 'photo.jpg'
    source.write_bytes(DATA * 10)

    codec = CodecMime(work_dir=str(tmp_path))
    encoded = codec.encode(str(source), mem_buf=1000)
    assert codec.headers['MIME-Version'] == '1.0'

    raw = b''.join(
        b'%s: %s\r\n' % (k.encode(), v.encode())
        for k, v in codec.headers.items()) + b'\r\n' + encoded.getvalue()
    _, contents = run_decoders(raw, decoders(tmp_path))
    assert contents[1].filename == 'photo.jpg'
    assert contents[1].content_type == 'image/jpeg'
    assert contents[1].getvalue() == DATA * 10

    # Every line is 76 characters or less
    assert max(len(line) for line in encoded.getvalue().split(b'\r\n')) \
        <= 76


def test_boundaries_can_be_spotted_on_their_own():
    """
    Without the article to go by, only boundary lines are recognised
    """
    codec = CodecMime()
    assert codec.detect(b'--==XYZ==\r\n', relative=False)['key'] == 'next'
    assert codec.detect(b'--==XYZ==--\r\n', relative=False)['key'] == \
        'last'
    assert codec.detect(b'-- \r\n', relative=False) is None
    assert codec.detect(b'just text\r\n', relative=False) is None


def test_fetched_articles_have_their_attachments_decoded(
        nntp_server, tmp_path):
    """
    Fetching a MIME article returns its parts decoded
    """
    path = tmp_path / 'mime.msg'
    path.write_bytes(MULTIPART)
    nntp_server.add_article('mime@x', str(path), GROUP)

    sock = connect_to(nntp_server)
    article = sock.get('mime@x', work_dir=str(tmp_path / 'work'),
                       group=GROUP)
    sock.close()

    found = {c.filename: c.getvalue() for c in article.decoded
             if getattr(c, 'content_type', '')}
    assert found == {
        '': b'Caf\xe9 is open today.\r\nSecond line', 'data.bin': DATA}


def test_nr_fetch_saves_attachments(tmp_path):
    """
    nr fetch saves the attachments of a MIME article under their names
    """
    from newsreap.cli import cli

    group_list = tmp_path / 'group.list'
    group_list.write_text('%s 0000000001 0000000001 y\n' % GROUP)

    with FakeNNTPServer(group_list=str(group_list),
                        join_group=False) as server:
        server.add_group(GROUP, 1, 1, 1)
        path = tmp_path / 'mime.msg'
        path.write_bytes(MULTIPART)
        server.add_article('mime@x', str(path), GROUP)

        config = config_for(server, tmp_path / 'config.yaml')
        out = tmp_path / 'out'
        result = CliRunner().invoke(
            cli, ['--config', config, 'fetch', 'mime@x', '-o', str(out)],
            obj={})

        assert result.exit_code == 0, result.output
        assert result.output.splitlines() == [str(out / 'data.bin')]
        assert (out / 'data.bin').read_bytes() == DATA
