ARG BUILD_FROM
FROM $BUILD_FROM

WORKDIR /app

COPY requirements.txt .

# Pillow needs jpeg/zlib/freetype at build time; if the platform's wheel
# is self-contained this build step is redundant but harmless, and if it
# isn't, this is what lets it compile instead of failing.
RUN apk add --no-cache --virtual .build-deps build-base jpeg-dev zlib-dev freetype-dev \
    && apk add --no-cache libjpeg-turbo zlib freetype \
    && pip install --no-cache-dir -r requirements.txt \
    && apk del .build-deps

COPY run.py akp05_device.py akp05_icons.py ./

CMD [ "python3", "/app/run.py" ]
