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
