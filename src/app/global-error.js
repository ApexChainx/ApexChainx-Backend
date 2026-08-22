"use client";

import * as Sentry from "@sentry/nextjs";
import NextError from "next/error";

export default function GlobalError({ error }) {
  Sentry.captureException(error);

  return (
    <html lang="en">
      <body>
        <NextError statusCode={500} />
      </body>
    </html>
  );
}
