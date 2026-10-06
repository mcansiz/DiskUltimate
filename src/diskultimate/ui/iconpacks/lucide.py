"""Lucide 1.48.0 — gomulu ikon verisi (OTOMATIK URETILDI).

Elle duzenlemeyin; `python3 tools/iconpacks.py` ile yeniden uretin.
Kaynak: https://www.npmjs.com/package/lucide-static (ISC).
Paketin lisans metni asagida `LICENSE` sabitindedir ve uygulamanin
"Ucuncu taraf lisanslari" penceresinde gosterilir.
"""
# flake8: noqa

TITLE = 'Lucide'
VERSION = '1.48.0'
LICENSE_NAME = 'ISC'
URL = 'https://www.npmjs.com/package/lucide-static'
LICENSE = 'ISC License\n\nCopyright (c) 2026 Lucide Icons and Contributors\n\nPermission to use, copy, modify, and/or distribute this software for any\npurpose with or without fee is hereby granted, provided that the above\ncopyright notice and this permission notice appear in all copies.\n\nTHE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES\nWITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF\nMERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR\nANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES\nWHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN\nACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF\nOR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.\n\n---\n\nThe following Lucide icons are derived from the Feather project:\n\nairplay, alert-circle, alert-octagon, alert-triangle, aperture, arrow-down-circle, arrow-down-left, arrow-down-right, arrow-down, arrow-left-circle, arrow-left, arrow-right-circle, arrow-right, arrow-up-circle, arrow-up-left, arrow-up-right, arrow-up, at-sign, calendar, cast, check, chevron-down, chevron-left, chevron-right, chevron-up, chevrons-down, chevrons-left, chevrons-right, chevrons-up, circle, clipboard, clock, code, columns, command, compass, corner-down-left, corner-down-right, corner-left-down, corner-left-up, corner-right-down, corner-right-up, corner-up-left, corner-up-right, crosshair, database, divide-circle, divide-square, dollar-sign, download, external-link, feather, frown, hash, headphones, help-circle, info, italic, key, layout, life-buoy, link-2, link, loader, lock, log-in, log-out, maximize, meh, minimize, minimize-2, minus-circle, minus-square, minus, monitor, moon, more-horizontal, more-vertical, move, music, navigation-2, navigation, octagon, pause-circle, percent, plus-circle, plus-square, plus, power, radio, rss, search, server, share, shopping-bag, sidebar, smartphone, smile, square, table-2, tablet, target, terminal, trash-2, trash, triangle, tv, type, upload, x-circle, x-octagon, x-square, x, zoom-in, zoom-out\n\nThe MIT License (MIT) (for the icons listed above)\n\nCopyright (c) 2013-present Cole Bemis\n\nPermission is hereby granted, free of charge, to any person obtaining a copy\nof this software and associated documentation files (the "Software"), to deal\nin the Software without restriction, including without limitation the rights\nto use, copy, modify, merge, publish, distribute, sublicense, and/or sell\ncopies of the Software, and to permit persons to whom the Software is\nfurnished to do so, subject to the following conditions:\n\nThe above copyright notice and this permission notice shall be included in all\ncopies or substantial portions of the Software.\n\nTHE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR\nIMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,\nFITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE\nAUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER\nLIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,\nOUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE\nSOFTWARE.'

# ad: (viewBox, [(yol, kip, cizgi_kalinligi, dolgu_kurali)])
# kip: 'f' dolgu, 's' cizgi; kural: 'e' evenodd
ICONS = {
    'apply': ((0.0, 0.0, 24.0, 24.0), [
        ('M4 22V4a1 1 0 0 1 .4-.8A6 6 0 0 1 8 2c3 0 5 2 7.333 2q2 0 3.067-.8A1 1 0 0 1 20 4v10a1 1 0 0 1-.4.8A6 6 0 0 1 16 16c-3 0-5-2-8-2a6 6 0 0 0-4 1.528', 's', 2.0, ''),
    ]),
    'backup': ((0.0, 0.0, 24.0, 24.0), [
        ('M3 3h18a1 1 0 0 1 1 1v3a1 1 0 0 1 -1 1h-18a1 1 0 0 1 -1 -1v-3a1 1 0 0 1 1 -1z', 's', 2.0, ''),
        ('M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8', 's', 2.0, ''),
        ('M10 12h4', 's', 2.0, ''),
    ]),
    'boot': ((0.0, 0.0, 24.0, 24.0), [
        ('M11.525 2.295a.53.53 0 0 1 .95 0l2.31 4.679a2.123 2.123 0 0 0 1.595 1.16l5.166.756a.53.53 0 0 1 .294.904l-3.736 3.638a2.123 2.123 0 0 0-.611 1.878l.882 5.14a.53.53 0 0 1-.771.56l-4.618-2.428a2.122 2.122 0 0 0-1.973 0L6.396 21.01a.53.53 0 0 1-.77-.56l.881-5.139a2.122 2.122 0 0 0-.611-1.879L2.16 9.795a.53.53 0 0 1 .294-.906l5.165-.755a2.122 2.122 0 0 0 1.597-1.16z', 's', 2.0, ''),
    ]),
    'boot-order': ((0.0, 0.0, 24.0, 24.0), [
        ('M11 5h10', 's', 2.0, ''),
        ('M11 12h10', 's', 2.0, ''),
        ('M11 19h10', 's', 2.0, ''),
        ('M4 4h1v5', 's', 2.0, ''),
        ('M4 9h2', 's', 2.0, ''),
        ('M6.5 20H3.4c0-1 2.6-1.925 2.6-3.5a1.5 1.5 0 0 0-2.6-1.02', 's', 2.0, ''),
    ]),
    'bootloader': ((0.0, 0.0, 24.0, 24.0), [
        ('M12 2v10', 's', 2.0, ''),
        ('M18.4 6.6a9 9 0 1 1-12.77.04', 's', 2.0, ''),
    ]),
    'clone': ((0.0, 0.0, 24.0, 24.0), [
        ('M10 8h10a2 2 0 0 1 2 2v10a2 2 0 0 1 -2 2h-10a2 2 0 0 1 -2 -2v-10a2 2 0 0 1 2 -2z', 's', 2.0, ''),
        ('M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2', 's', 2.0, ''),
    ]),
    'convert': ((0.0, 0.0, 24.0, 24.0), [
        ('M8 3 4 7l4 4', 's', 2.0, ''),
        ('M4 7h16', 's', 2.0, ''),
        ('m16 21 4-4-4-4', 's', 2.0, ''),
        ('M20 17H4', 's', 2.0, ''),
    ]),
    'discard': ((0.0, 0.0, 24.0, 24.0), [
        ('M2 12a10 10 0 1 0 20 0a10 10 0 1 0 -20 0z', 's', 2.0, ''),
        ('m15 9-6 6', 's', 2.0, ''),
        ('m9 9 6 6', 's', 2.0, ''),
    ]),
    'disk': ((0.0, 0.0, 24.0, 24.0), [
        ('M10 16h.01', 's', 2.0, ''),
        ('M2.212 11.577a2 2 0 0 0-.212.896V18a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-5.527a2 2 0 0 0-.212-.896L18.55 5.11A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z', 's', 2.0, ''),
        ('M21.946 12.013H2.054', 's', 2.0, ''),
        ('M6 16h.01', 's', 2.0, ''),
    ]),
    'disk-removable': ((0.0, 0.0, 24.0, 24.0), [
        ('M9 7a1 1 0 1 0 2 0a1 1 0 1 0 -2 0z', 's', 2.0, ''),
        ('M3 20a1 1 0 1 0 2 0a1 1 0 1 0 -2 0z', 's', 2.0, ''),
        ('M4.7 19.3 19 5', 's', 2.0, ''),
        ('m21 3-3 1 2 2Z', 's', 2.0, ''),
        ('M9.26 7.68 5 12l2 5', 's', 2.0, ''),
        ('m10 14 5 2 3.5-3.5', 's', 2.0, ''),
        ('m18 12 1-1 1 1-1 1Z', 's', 2.0, ''),
    ]),
    'disk-system': ((0.0, 0.0, 24.0, 24.0), [
        ('M4 2h16a2 2 0 0 1 2 2v4a2 2 0 0 1 -2 2h-16a2 2 0 0 1 -2 -2v-4a2 2 0 0 1 2 -2z', 's', 2.0, ''),
        ('M4 14h16a2 2 0 0 1 2 2v4a2 2 0 0 1 -2 2h-16a2 2 0 0 1 -2 -2v-4a2 2 0 0 1 2 -2z', 's', 2.0, ''),
        ('M6 6L6.01 6', 's', 2.0, ''),
        ('M6 18L6.01 18', 's', 2.0, ''),
    ]),
    'efi': ((0.0, 0.0, 24.0, 24.0), [
        ('M12 20v2', 's', 2.0, ''),
        ('M12 2v2', 's', 2.0, ''),
        ('M17 20v2', 's', 2.0, ''),
        ('M17 2v2', 's', 2.0, ''),
        ('M2 12h2', 's', 2.0, ''),
        ('M2 17h2', 's', 2.0, ''),
        ('M2 7h2', 's', 2.0, ''),
        ('M20 12h2', 's', 2.0, ''),
        ('M20 17h2', 's', 2.0, ''),
        ('M20 7h2', 's', 2.0, ''),
        ('M7 20v2', 's', 2.0, ''),
        ('M7 2v2', 's', 2.0, ''),
        ('M6 4h12a2 2 0 0 1 2 2v12a2 2 0 0 1 -2 2h-12a2 2 0 0 1 -2 -2v-12a2 2 0 0 1 2 -2z', 's', 2.0, ''),
        ('M9 8h6a1 1 0 0 1 1 1v6a1 1 0 0 1 -1 1h-6a1 1 0 0 1 -1 -1v-6a1 1 0 0 1 1 -1z', 's', 2.0, ''),
    ]),
    'export': ((0.0, 0.0, 24.0, 24.0), [
        ('M12 3v12', 's', 2.0, ''),
        ('m17 8-5-5-5 5', 's', 2.0, ''),
        ('M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4', 's', 2.0, ''),
    ]),
    'file': ((0.0, 0.0, 24.0, 24.0), [
        ('M6 22a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h8a2.4 2.4 0 0 1 1.704.706l3.588 3.588A2.4 2.4 0 0 1 20 8v12a2 2 0 0 1-2 2z', 's', 2.0, ''),
        ('M14 2v5a1 1 0 0 0 1 1h5', 's', 2.0, ''),
    ]),
    'folder': ((0.0, 0.0, 24.0, 24.0), [
        ('M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z', 's', 2.0, ''),
    ]),
    'folder-add': ((0.0, 0.0, 24.0, 24.0), [
        ('M2 9V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H20a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-1', 's', 2.0, ''),
        ('M2 13h10', 's', 2.0, ''),
        ('m9 16 3-3-3-3', 's', 2.0, ''),
    ]),
    'folder-new': ((0.0, 0.0, 24.0, 24.0), [
        ('M12 10v6', 's', 2.0, ''),
        ('M9 13h6', 's', 2.0, ''),
        ('M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z', 's', 2.0, ''),
    ]),
    'format': ((0.0, 0.0, 24.0, 24.0), [
        ('m14.622 17.897-10.68-2.913', 's', 2.0, ''),
        ('M18.376 2.622a1 1 0 1 1 3.002 3.002L17.36 9.643a.5.5 0 0 0 0 .707l.944.944a2.41 2.41 0 0 1 0 3.408l-.944.944a.5.5 0 0 1-.707 0L8.354 7.348a.5.5 0 0 1 0-.707l.944-.944a2.41 2.41 0 0 1 3.408 0l.944.944a.5.5 0 0 0 .707 0z', 's', 2.0, ''),
        ('M9 8c-1.804 2.71-3.97 3.46-6.583 3.948a.507.507 0 0 0-.302.819l7.32 8.883a1 1 0 0 0 1.185.204C12.735 20.405 16 16.792 16 15', 's', 2.0, ''),
    ]),
    'fs-repair': ((0.0, 0.0, 24.0, 24.0), [
        ('M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.106-3.105c.32-.322.863-.22.983.218a6 6 0 0 1-8.259 7.057l-7.91 7.91a1 1 0 0 1-2.999-3l7.91-7.91a6 6 0 0 1 7.057-8.259c.438.12.54.662.219.984z', 's', 2.0, ''),
    ]),
    'image': ((0.0, 0.0, 24.0, 24.0), [
        ('M2 12a10 10 0 1 0 20 0a10 10 0 1 0 -20 0z', 's', 2.0, ''),
        ('M10 12a2 2 0 1 0 4 0a2 2 0 1 0 -4 0z', 's', 2.0, ''),
    ]),
    'image-resize': ((0.0, 0.0, 24.0, 24.0), [
        ('M12 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7', 's', 2.0, ''),
        ('M14 15H9v-5', 's', 2.0, ''),
        ('M16 3h5v5', 's', 2.0, ''),
        ('M21 3 9 15', 's', 2.0, ''),
    ]),
    'import': ((0.0, 0.0, 24.0, 24.0), [
        ('M12 15V3', 's', 2.0, ''),
        ('M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4', 's', 2.0, ''),
        ('m7 10 5 5 5-5', 's', 2.0, ''),
    ]),
    'info': ((0.0, 0.0, 24.0, 24.0), [
        ('M2 12a10 10 0 1 0 20 0a10 10 0 1 0 -20 0z', 's', 2.0, ''),
        ('M12 16v-4', 's', 2.0, ''),
        ('M12 8h.01', 's', 2.0, ''),
    ]),
    'label': ((0.0, 0.0, 24.0, 24.0), [
        ('M12.586 2.586A2 2 0 0 0 11.172 2H4a2 2 0 0 0-2 2v7.172a2 2 0 0 0 .586 1.414l8.704 8.704a2.426 2.426 0 0 0 3.42 0l6.58-6.58a2.426 2.426 0 0 0 0-3.42z', 's', 2.0, ''),
        ('M7 7.5a0.5 0.5 0 1 0 1 0a0.5 0.5 0 1 0 -1 0z', 'fs', 2.0, ''),
    ]),
    'mount': ((0.0, 0.0, 24.0, 24.0), [
        ('M12 22v-5', 's', 2.0, ''),
        ('M15 8V2', 's', 2.0, ''),
        ('M17 8a1 1 0 0 1 1 1v4a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V9a1 1 0 0 1 1-1z', 's', 2.0, ''),
        ('M9 8V2', 's', 2.0, ''),
    ]),
    'open': ((0.0, 0.0, 24.0, 24.0), [
        ('m6 14 1.5-2.9A2 2 0 0 1 9.24 10H20a2 2 0 0 1 1.94 2.5l-1.54 6a2 2 0 0 1-1.95 1.5H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H18a2 2 0 0 1 2 2v2', 's', 2.0, ''),
    ]),
    'partition': ((0.0, 0.0, 24.0, 24.0), [
        ('M21 12c.552 0 1.005-.449.95-.998a10 10 0 0 0-8.953-8.951c-.55-.055-.998.398-.998.95v8a1 1 0 0 0 1 1z', 's', 2.0, ''),
        ('M21.21 15.89A10 10 0 1 1 8 2.83', 's', 2.0, ''),
    ]),
    'partition-delete': ((0.0, 0.0, 24.0, 24.0), [
        ('M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1 -2 2h-14a2 2 0 0 1 -2 -2v-14a2 2 0 0 1 2 -2z', 's', 2.0, ''),
        ('M8 12h8', 's', 2.0, ''),
    ]),
    'partition-new': ((0.0, 0.0, 24.0, 24.0), [
        ('M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1 -2 2h-14a2 2 0 0 1 -2 -2v-14a2 2 0 0 1 2 -2z', 's', 2.0, ''),
        ('M8 12h8', 's', 2.0, ''),
        ('M12 8v8', 's', 2.0, ''),
    ]),
    'pending': ((0.0, 0.0, 24.0, 24.0), [
        ('M5 22h14', 's', 2.0, ''),
        ('M5 2h14', 's', 2.0, ''),
        ('M17 22v-4.172a2 2 0 0 0-.586-1.414L12 12l-4.414 4.414A2 2 0 0 0 7 17.828V22', 's', 2.0, ''),
        ('M7 2v4.172a2 2 0 0 0 .586 1.414L12 12l4.414-4.414A2 2 0 0 0 17 6.172V2', 's', 2.0, ''),
    ]),
    'recover': ((0.0, 0.0, 24.0, 24.0), [
        ('M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8', 's', 2.0, ''),
        ('M3 3v5h5', 's', 2.0, ''),
        ('M12 7v5l4 2', 's', 2.0, ''),
    ]),
    'redo': ((0.0, 0.0, 24.0, 24.0), [
        ('m15 14 5-5-5-5', 's', 2.0, ''),
        ('M20 9H9.5A5.5 5.5 0 0 0 4 14.5A5.5 5.5 0 0 0 9.5 20H13', 's', 2.0, ''),
    ]),
    'refresh': ((0.0, 0.0, 24.0, 24.0), [
        ('M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8', 's', 2.0, ''),
        ('M21 3v5h-5', 's', 2.0, ''),
        ('M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16', 's', 2.0, ''),
        ('M8 16H3v5', 's', 2.0, ''),
    ]),
    'rename': ((0.0, 0.0, 24.0, 24.0), [
        ('M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z', 's', 2.0, ''),
        ('m15 5 4 4', 's', 2.0, ''),
    ]),
    'resize': ((0.0, 0.0, 24.0, 24.0), [
        ('m18 8 4 4-4 4', 's', 2.0, ''),
        ('M2 12h20', 's', 2.0, ''),
        ('m6 8-4 4 4 4', 's', 2.0, ''),
    ]),
    'save': ((0.0, 0.0, 24.0, 24.0), [
        ('M15.2 3a2 2 0 0 1 1.4.6l3.8 3.8a2 2 0 0 1 .6 1.4V19a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z', 's', 2.0, ''),
        ('M17 21v-7a1 1 0 0 0-1-1H8a1 1 0 0 0-1 1v7', 's', 2.0, ''),
        ('M7 3v4a1 1 0 0 0 1 1h7', 's', 2.0, ''),
    ]),
    'search': ((0.0, 0.0, 24.0, 24.0), [
        ('m21 21-4.34-4.34', 's', 2.0, ''),
        ('M3 11a8 8 0 1 0 16 0a8 8 0 1 0 -16 0z', 's', 2.0, ''),
    ]),
    'shield': ((0.0, 0.0, 24.0, 24.0), [
        ('M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z', 's', 2.0, ''),
        ('m9 12 2 2 4-4', 's', 2.0, ''),
    ]),
    'stop': ((0.0, 0.0, 24.0, 24.0), [
        ('M2 12a10 10 0 1 0 20 0a10 10 0 1 0 -20 0z', 's', 2.0, ''),
        ('M10 9h4a1 1 0 0 1 1 1v4a1 1 0 0 1 -1 1h-4a1 1 0 0 1 -1 -1v-4a1 1 0 0 1 1 -1z', 's', 2.0, ''),
    ]),
    'table': ((0.0, 0.0, 24.0, 24.0), [
        ('M12 3v18', 's', 2.0, ''),
        ('M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1 -2 2h-14a2 2 0 0 1 -2 -2v-14a2 2 0 0 1 2 -2z', 's', 2.0, ''),
        ('M3 9h18', 's', 2.0, ''),
        ('M3 15h18', 's', 2.0, ''),
    ]),
    'table-clear': ((0.0, 0.0, 24.0, 24.0), [
        ('M12 3v17a1 1 0 0 1-1 1H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v6a1 1 0 0 1-1 1H3', 's', 2.0, ''),
        ('m16.5 16.5 5 5', 's', 2.0, ''),
        ('m16.5 21.5 5-5', 's', 2.0, ''),
    ]),
    'trash': ((0.0, 0.0, 24.0, 24.0), [
        ('M10 11v6', 's', 2.0, ''),
        ('M14 11v6', 's', 2.0, ''),
        ('M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6', 's', 2.0, ''),
        ('M3 6h18', 's', 2.0, ''),
        ('M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2', 's', 2.0, ''),
    ]),
    'type': ((0.0, 0.0, 24.0, 24.0), [
        ('M4 9L20 9', 's', 2.0, ''),
        ('M4 15L20 15', 's', 2.0, ''),
        ('M10 3L8 21', 's', 2.0, ''),
        ('M16 3L14 21', 's', 2.0, ''),
    ]),
    'undo': ((0.0, 0.0, 24.0, 24.0), [
        ('M9 14 4 9l5-5', 's', 2.0, ''),
        ('M4 9h10.5a5.5 5.5 0 0 1 5.5 5.5a5.5 5.5 0 0 1-5.5 5.5H11', 's', 2.0, ''),
    ]),
    'unmount': ((0.0, 0.0, 24.0, 24.0), [
        ('m19 5 3-3', 's', 2.0, ''),
        ('m2 22 3-3', 's', 2.0, ''),
        ('M6.3 20.3a2.4 2.4 0 0 0 3.4 0L12 18l-6-6-2.3 2.3a2.4 2.4 0 0 0 0 3.4Z', 's', 2.0, ''),
        ('M7.5 13.5 10 11', 's', 2.0, ''),
        ('M10.5 16.5 13 14', 's', 2.0, ''),
        ('m12 6 6 6 2.3-2.3a2.4 2.4 0 0 0 0-3.4l-2.6-2.6a2.4 2.4 0 0 0-3.4 0Z', 's', 2.0, ''),
    ]),
    'up': ((0.0, 0.0, 24.0, 24.0), [
        ('m5 12 7-7 7 7', 's', 2.0, ''),
        ('M12 19V5', 's', 2.0, ''),
    ]),
    'warning': ((0.0, 0.0, 24.0, 24.0), [
        ('m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3', 's', 2.0, ''),
        ('M12 9v4', 's', 2.0, ''),
        ('M12 17h.01', 's', 2.0, ''),
    ]),
    'wipe': ((0.0, 0.0, 24.0, 24.0), [
        ('M21 21H8a2 2 0 0 1-1.42-.587l-3.994-3.999a2 2 0 0 1 0-2.828l10-10a2 2 0 0 1 2.829 0l5.999 6a2 2 0 0 1 0 2.828L12.834 21', 's', 2.0, ''),
        ('m5.082 11.09 8.828 8.828', 's', 2.0, ''),
    ]),
    'wipe-free': ((0.0, 0.0, 24.0, 24.0), [
        ('m16 22-1-4', 's', 2.0, ''),
        ('M19 14a1 1 0 0 0 1-1v-1a2 2 0 0 0-2-2h-3a1 1 0 0 1-1-1V4a2 2 0 0 0-4 0v5a1 1 0 0 1-1 1H6a2 2 0 0 0-2 2v1a1 1 0 0 0 1 1', 's', 2.0, ''),
        ('M19 14H5l-1.973 6.767A1 1 0 0 0 4 22h16a1 1 0 0 0 .973-1.233z', 's', 2.0, ''),
        ('m8 22 1-4', 's', 2.0, ''),
    ]),
}
