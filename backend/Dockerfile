FROM node:22-alpine
WORKDIR /app/backend
COPY backend/package.json ./
COPY backend/src ./src
COPY frontend/src/lib/simulator.ts /app/frontend/src/lib/simulator.ts
COPY frontend/src/types.ts /app/frontend/src/types.ts
RUN mkdir -p /app/data && chown node:node /app/data
USER node
ENV QCAL_HOST=0.0.0.0 QCAL_PORT=5290 QCAL_DATA_FILE=/app/data/state.json QCAL_DEMO_DATA_FILE=/app/data/workflows.json
EXPOSE 5290
CMD ["node", "--experimental-strip-types", "src/server.mjs"]
