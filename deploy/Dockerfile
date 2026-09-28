# CCXT-Fr image: this repo's freqtrade checkout (exact commit used for backtests and dry-runs) + its FreqUI build.
# Build context = repo root:  docker build -f user_data/deploy/Dockerfile -t ccxt-freqtrade:local .
# user_data is NOT baked in; it is bind-mounted at /freqtrade/user_data at run time.
FROM python:3.13-slim-trixie AS base
ENV LANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 PYTHONFAULTHANDLER=1 \
    PATH=/home/ftuser/.local/bin:$PATH FT_APP_ENV="docker"
RUN mkdir /freqtrade \
  && apt-get update \
  && apt-get -y install --no-install-recommends libatlas3-base curl sqlite3 libgomp1 \
  && apt-get clean && rm -rf /var/lib/apt/lists/* \
  && useradd -u 1000 -U -m -s /bin/bash ftuser \
  && chown ftuser:ftuser /freqtrade
WORKDIR /freqtrade

FROM base AS python-deps
RUN apt-get update \
  && apt-get -y install --no-install-recommends build-essential libssl-dev git libffi-dev libgfortran5 pkg-config cmake gcc \
  && apt-get clean && rm -rf /var/lib/apt/lists/* \
  && pip install --upgrade pip wheel
COPY --chown=ftuser:ftuser requirements.txt requirements-hyperopt.txt /freqtrade/
USER ftuser
RUN pip install --user --no-cache-dir "numpy<3.0" \
  && pip install --user --no-cache-dir -r requirements-hyperopt.txt

FROM base AS runtime-image
COPY --from=python-deps --chown=ftuser:ftuser /home/ftuser/.local /home/ftuser/.local
USER ftuser
COPY --chown=ftuser:ftuser . /freqtrade/
# FreqUI comes from the checkout (freqtrade/rpc/api_server/ui/installed), so no GitHub download at build time
RUN pip install -e . --user --no-cache-dir \
  && mkdir -p /freqtrade/user_data \
  && test -f /freqtrade/freqtrade/rpc/api_server/ui/installed/index.html
ENTRYPOINT ["freqtrade"]
CMD ["trade"]
