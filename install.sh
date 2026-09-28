#!/bin/sh

install -d -m 755 $DESTDIR/usr/bin/
install -d -m 755 $DESTDIR/usr/share/pixmaps
install -d -m 755 $DESTDIR/usr/share/applications
install -m 644 youtube_player.desktop $DESTDIR/usr/share/applications/
install -m 755 src/youtube_player.py $DESTDIR/usr/bin/
install -m 755 src/yt-dlp $DESTDIR/usr/bin/
install -m 644 src/youtube_player.png $DESTDIR/usr/share/pixmaps/

for i in `ls po/*.mo|sed "s|po/\(.*\).mo|\1|"`; do
	install -d -m 755 $DESTDIR/usr/share/locale/${i}/LC_MESSAGES
	install -m 644 po/${i}.mo \
	$DESTDIR/usr/share/locale/${i}/LC_MESSAGES/youtube_player.mo
done
