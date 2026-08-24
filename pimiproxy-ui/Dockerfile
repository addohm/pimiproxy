# glibc bases (Debian slim / nginx Debian) — NOT alpine/musl, so the UI runs
# across the whole Pi range (4 KB and 16 KB page kernels alike).
FROM node:20-slim AS build
WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .
RUN npm run build

FROM nginx:1.27
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
