FROM scratch AS release
ADD --chmod=0555 --checksum=sha256:f6dc6ba676e5ac96a565ebc8d657232f94c6158e85b4352141bd1a46f6b43434 https://github.com/FreeCAD/FreeCAD/releases/download/1.1.4/FreeCAD_1.1.4-Linux-x86_64-py311.AppImage /FreeCAD.AppImage

FROM python:3.11-slim-bookworm@sha256:e8c69d86735f6df3efa06b324694fe51f0422785f83a3bd463052636c2421944
RUN sed -i 's|http://deb.debian.org/debian-security|https://snapshot.debian.org/archive/debian-security/20261119T000000Z|; s|http://deb.debian.org/debian|https://snapshot.debian.org/archive/debian/20261119T000000Z|' /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Check-Valid-Until=false update \
    && apt-get install -y --no-install-recommends libgl1 libegl1 libxrender1 libsm6 && apt-get clean
WORKDIR /opt
RUN --mount=type=bind,from=release,source=/FreeCAD.AppImage,target=/tmp/FreeCAD.AppImage /tmp/FreeCAD.AppImage --appimage-extract >/dev/null && mv squashfs-root freecad
ENV HOME=/tmp PYTHONDONTWRITEBYTECODE=1 APPDIR=/opt/freecad LD_LIBRARY_PATH=/opt/freecad/usr/lib PYTHONPATH=/opt/freecad/usr/lib
WORKDIR /opt/probe
COPY export_step.py verify_step.py ./
USER 65534:65534
CMD ["timeout", "60", "sh", "-ec", "/opt/freecad/usr/bin/python export_step.py /tmp/plate.step && /opt/freecad/usr/bin/python verify_step.py /tmp/plate.step"]
