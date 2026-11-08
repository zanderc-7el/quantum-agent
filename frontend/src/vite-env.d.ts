/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** "remote" switches the gateway from the local simulator to the QuantaMind API. */
  readonly VITE_GATEWAY_MODE?: string;
  readonly VITE_API_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
