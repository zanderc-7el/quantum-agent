FROM node:22-alpine AS build
WORKDIR /src/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
COPY prototype/ ../prototype/
COPY backend/ ../backend/
COPY compose.yaml ../compose.yaml
RUN npm test && npm run build

FROM nginx:stable-alpine
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/frontend/dist /usr/share/nginx/html
EXPOSE 80
