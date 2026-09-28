#!/bin/sh

intltool-extract --type="gettext/ini" youtube_player.desktop.in
xgettext --from-code=utf-8 -L shell -o po/youtube_player.pot src/youtube_player.py
xgettext --from-code=utf-8 -j -L C -kN_ -o po/youtube_player.pot youtube_player.desktop.in.h

rm youtube_player.desktop.in.h

cd po
for i in `ls *.po`; do
	msgmerge -U $i youtube_player.pot
done
rm -f ./*~
