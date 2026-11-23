#!/bin/bash
# build.sh: gabcb.so from gabcb.cpp against the CBot headers of the Colobot release the adapter runs (colobot-gold-0.2.1-alpha).
# Needs g++ and curl (host side; the prebuilt gabcb.so next to this file is what install() copies into the sandbox).
set -e; cd "$(dirname "$0")"; H=$(mktemp -d); TAG=colobot-gold-0.2.1-alpha
for f in CBot/CBotDefines.h CBot/CBotTypResult.h CBot/CBotEnums.h CBot/CBotUtils.h CBot/CBotVar/CBotVar.h CBot/CBotClass.h \
         CBot/CBotProgram.h CBot/CBotVar/CBotVarClass.h CBot/CBotToken.h CBot/CBotCStack.h CBot/CBotFileUtils.h CBot/CBotExternalCall.h; do
  mkdir -p $H/$(dirname $f); curl -sfL -o $H/$f https://raw.githubusercontent.com/colobot/colobot/$TAG/src/$f
done
g++ -std=c++14 -O2 -fPIC -shared -I$H -o gabcb.so gabcb.cpp -ldl
rm -rf $H; ls -l gabcb.so
