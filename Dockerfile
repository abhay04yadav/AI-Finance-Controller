# The API, as a long-running container.
#
# Deliberately NOT a serverless function. This service holds state a
# reconciliation session depends on: the journal repository, the review
# decisions and the audit trail all live in process memory, so approving an
# entry on /review and then opening /books only agrees with itself if both
# requests reach the same process. Serverless would hand the second request a
# fresh instance and the approval would vanish.
#
# The datasets ship in the image. A container cannot run `make generate` on
# boot — and it should not: the demo must serve the same two seeds every time,
# so a judge comparing the hosted screen against the repo sees one answer.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Kolkata

WORKDIR /app

# pyproject declares fourteen packages explicitly, so the whole tree has to be
# present before the install — splitting deps into an earlier layer would need
# the package list duplicated in this file, and a duplicated list is one that
# goes stale.
COPY . .
RUN pip install --no-cache-dir .

# Fail the BUILD, not the first request, if the seeds the demo serves are
# missing. Without them every endpoint returns 500 and the cause is three
# layers down in a log nobody is watching.
RUN test -f data/seed42/truth.json && test -f data/seed7/truth.json

EXPOSE 8000

# $PORT is what Render, Railway and Fly all inject; 8000 is the local default.
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
