#!/bin/sh

back(){
	cd /storage/.config/back
	unzip -o back.zip
	cp -f Twin* /storage/joypads
	cp -f Twin* /tmp/joypads
	cp -f retroarch.cfg /storage/.config/retroarch
	cp -f es_input.cfg /storage/.config/emulationstation
	cp -f es_systems.cfg /storage/.config/emulationstation
	cp -f es_settings.cfg /storage/.config/emulationstation
	cp -f emuelec.conf /storage/.config/emuelec/configs
	cp -f drastic.cfg /storage/.config/emulationstation/scripts/drastic/config
}

back