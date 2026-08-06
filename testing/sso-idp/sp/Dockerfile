FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends apache2 libapache2-mod-auth-mellon curl openssl ca-certificates \
 && rm -rf /var/lib/apt/lists/*
RUN a2enmod auth_mellon proxy proxy_http headers
COPY mellon-base.conf /etc/apache2/conf-available/mellon-base.conf
COPY vhost.conf       /etc/apache2/sites-available/000-default.conf
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
EXPOSE 80
CMD ["/entrypoint.sh"]
