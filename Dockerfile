FROM python:3.11

RUN curl https://sh.rustup.rs -sSf | sh -s -- -y --default-toolchain stable
ENV PATH="/root/.cargo/bin:$PATH"

WORKDIR /srv

COPY requirements.txt .
RUN pip3 install -r requirements.txt

COPY . .
CMD [ "python3", "./main.py"]
