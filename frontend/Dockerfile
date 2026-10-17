FROM nginx:1.29-alpine

COPY frontend/index.html frontend/styles.css frontend/app.js /usr/share/nginx/html/
COPY infrastructure/nginx/frontend-config.js /usr/share/nginx/html/config.js
COPY infrastructure/nginx/default.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

