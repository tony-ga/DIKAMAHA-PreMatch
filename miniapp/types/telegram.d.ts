export {};

declare global {
  interface Window {
    Telegram?: {
      WebApp: {
        initData: string;
        colorScheme: "light" | "dark";
        ready(): void;
        expand(): void;
        close(): void;
        onEvent(event: string, callback: () => void): void;
        offEvent(event: string, callback: () => void): void;
        // Abre un link de Telegram en el cliente nativo en vez del WebView.
        // Es la única forma de invocar la hoja de "reenviar a…" desde dentro
        // de una Mini App; `navigator.share` no está disponible en el WebView
        // de Telegram en Android.
        openTelegramLink?(url: string): void;
        // Abre la hoja de pago nativa de Telegram sobre un enlace de factura
        // creado con `createInvoiceLink`. El callback informa del resultado en
        // el cliente, pero **no es la confirmación**: el asiento llega por
        // `successful_payment` al bot, así que tras un `paid` hay que
        // reconsultar la titularidad en vez de darla por hecha.
        openInvoice?(
          url: string,
          callback?: (status: "paid" | "cancelled" | "failed" | "pending") => void,
        ): void;
        BackButton: {
          show(): void;
          hide(): void;
          onClick(callback: () => void): void;
          offClick(callback: () => void): void;
        };
      };
    };
  }
}
