ARG BUILD_FROM
FROM $BUILD_FROM

WORKDIR /app

# Otherwise Python block-buffers stdout when it isn't a TTY, and the
# add-on's Log tab lags minutes behind (or never shows "Connected to
# MQTT broker" at all) -- which made a working add-on look stuck.
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .

# Pillow needs jpeg/zlib/freetype at build time; if the platform's wheel
# is self-contained this build step is redundant but harmless, and if it
# isn't, this is what lets it compile instead of failing.
RUN apk add --no-cache --virtual .build-deps build-base jpeg-dev zlib-dev freetype-dev \
    && apk add --no-cache libjpeg-turbo zlib freetype \
    && pip install --no-cache-dir -r requirements.txt \
    && apk del .build-deps

# config.yaml comes along so run.py can print its own version at startup
# -- a log with no version in it can't be matched to a build, which is
# exactly how two rounds of fixes got debugged against an older image.
COPY run.py akp05_device.py akp05_icons.py config.yaml ./

# Bake the MDI + Roboto fonts into the image rather than fetching them on
# the first icon/text render. They cache next to akp05_icons.py, i.e. in
# this image layer, so a rebuild wiped them and left every icon and text
# set failing until jsdelivr/GitHub were reachable again -- while button
# presses kept working (they never touch the font code), and neither an
# add-on restart nor a replug made any difference. A download failure now
# breaks the build loudly instead of surfacing as that at runtime.
RUN python3 -c "import akp05_icons; akp05_icons.prefetch_fonts()"

CMD [ "python3", "/app/run.py" ]
