# ---- Build stage ----
FROM node:20-alpine AS build
WORKDIR /app

COPY package.json ./
# Use npm install (no lockfile in repo) to resolve deps.
RUN npm install

COPY tsconfig.json vite.config.ts tailwind.config.js postcss.config.js index.html ./
COPY src ./src

RUN npm run build

# ---- Runtime stage ----
FROM nginx:1.27-alpine

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=15s --timeout=3s --retries=5 \
  CMD wget -qO- http://localhost/ >/dev/null || exit 1
