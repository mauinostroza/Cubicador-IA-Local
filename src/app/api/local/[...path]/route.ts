import { NextRequest, NextResponse } from 'next/server';

export const runtime = 'nodejs';

const DEFAULT_BACKEND = 'http://127.0.0.1:8765';

function backendUrl(path: string[]): URL {
  const configured = process.env.CUBICADOR_BACKEND_URL || DEFAULT_BACKEND;
  let base: URL;
  try {
    base = new URL(configured);
  } catch {
    throw new Error('La URL del backend local no es válida');
  }
  // This proxy is intentionally unable to target the Internet, LAN, or a
  // hostname that could resolve somewhere unexpected.
  if (base.protocol !== 'http:' || base.hostname !== '127.0.0.1' || base.username || base.password || base.search || base.hash) {
    throw new Error('El backend debe escuchar exclusivamente en 127.0.0.1');
  }
  const port = base.port ? Number(base.port) : 80;
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error('Puerto del backend local inválido');
  }
  base.pathname = `/${path.map((part) => encodeURIComponent(part)).join('/')}`;
  return base;
}

async function proxy(request: NextRequest, path: string[]) {
  let target: URL;
  try {
    target = backendUrl(path);
  } catch (error) {
    return NextResponse.json({ error: error instanceof Error ? error.message : 'Backend local inválido', code: 'LOCAL_BACKEND_INVALID' }, { status: 503 });
  }
  try {
    const headers = new Headers();
    const contentType = request.headers.get('content-type');
    if (contentType) headers.set('content-type', contentType);
    const contentLength = request.headers.get('content-length');
    if (contentLength) headers.set('content-length', contentLength);
    const token = process.env.CUBICADOR_BACKEND_TOKEN;
    if (!token) {
      return NextResponse.json({ error: 'Falta el token del backend local', code: 'LOCAL_BACKEND_NOT_CONFIGURED' }, { status: 503 });
    }
    headers.set('authorization', `Bearer ${token}`);
    const response = await fetch(target, {
      method: request.method,
      headers,
      body: request.method === 'GET' || request.method === 'HEAD' ? undefined : request.body,
      cache: 'no-store',
      signal: AbortSignal.timeout(request.method === 'POST' ? 15000 : 3000),
    });
    const responseHeaders = new Headers();
    const responseType = response.headers.get('content-type');
    if (responseType) responseHeaders.set('content-type', responseType);
    const disposition = response.headers.get('content-disposition');
    if (disposition) responseHeaders.set('content-disposition', disposition);
    responseHeaders.set('cache-control', 'no-store');
    return new NextResponse(response.body, { status: response.status, headers: responseHeaders });
  } catch {
    return NextResponse.json({ error: 'Backend local no disponible. Inicie el servicio offline.', code: 'LOCAL_BACKEND_UNAVAILABLE' }, { status: 503 });
  }
}

export async function GET(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  return proxy(request, (await context.params).path);
}

export async function POST(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  return proxy(request, (await context.params).path);
}

export async function DELETE(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  return proxy(request, (await context.params).path);
}
