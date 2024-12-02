#############################
## Build main API container
#############################

# Set official image -- parent image
FROM python:3.12-alpine as base

ARG USERNAME=user
ARG USER_UID=1001
ARG GROUPNAME=$USERNAME
ARG USER_GID=$USER_UID

# Update and upgrade
RUN apk update && apk upgrade

# Install required dependencies...
RUN apk add g++ gcc musl-dev libffi-dev

# Create the user
RUN addgroup -g $USER_GID $GROUPNAME \
    && adduser -D -u $USER_UID -G $GROUPNAME $USERNAME

# Copy the content to a code folder in container
COPY ./requirements.txt /code/requirements.txt

# Install all dependencies
RUN pip3 install -r /code/requirements.txt

# Copy the content to a code folder in container - The owner is the user created
COPY --chown=$USER_UID:$USER_GID . /code

# Add code directory in pythonpath
ENV PYTHONPATH /code

#########################
## PRODUCTION CONTAINER
#########################
FROM base as production

RUN pip install gunicorn

# Add parameters for gunicorn
ENV GUNICORN_CMD_ARGS "--bind=0.0.0.0:5000 --workers=2 --thread=4 --worker-class=gthread --forwarded-allow-ips='*' --access-logfile -"

# Set working directory - 'code' dir in container, 'code' dir locally (in code)
WORKDIR /code

# Switch to the user
USER $USERNAME

# Run app -- needs to be in WORKDIR
CMD ["gunicorn", "run_app:app_obj"]
