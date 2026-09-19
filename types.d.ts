interface PaymentProof {
  product_id: string;
  razorpay_order_id: string;
  razorpay_payment_id: string;
  razorpay_signature: string;
}

interface Window {
  RED_RISING_AUDIO_CATALOG?: Record<string, { previewFile?: string }>;
}

declare class Razorpay {
  constructor(options: {
    key: string;
    amount: number;
    currency: string;
    name: string;
    description: string;
    image: string;
    order_id: string;
    theme: { color: string };
    modal: { ondismiss: () => void };
    handler: (response: Omit<PaymentProof, 'product_id'>) => Promise<void>;
  });
  on(event: 'payment.failed', handler: () => void): void;
  open(): void;
}
