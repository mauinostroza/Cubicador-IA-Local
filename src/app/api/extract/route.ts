import { NextResponse } from 'next/server';

export const runtime = 'nodejs';

/** Cerrado: la UI deberá conectarse únicamente al backend Python local. */
export async function POST() {
  return NextResponse.json(
    {
      error: 'Ruta deshabilitada: use exclusivamente el backend Python local y offline.',
      code: 'LOCAL_BACKEND_REQUIRED',
    },
    { status: 410 }
  );
}
