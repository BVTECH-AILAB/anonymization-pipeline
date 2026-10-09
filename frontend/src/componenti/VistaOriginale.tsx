// Il documento originale, dal File caricato (non passa dal server): pagina PDF con pdf.js, immagine,
// oppure il testo.
import { useEffect, useRef, useState } from "react";
import * as pdfjs from "pdfjs-dist";
import worker from "pdfjs-dist/build/pdf.worker.min.mjs?url";

pdfjs.GlobalWorkerOptions.workerSrc = worker;

const IMMAGINI_BROWSER = /\.(png|jpe?g|bmp|webp|gif)$/i;

function VistaPdf({ file, pagina, onPagine }: { file: File; pagina: number; onPagine?: (n: number) => void }) {
  const tela = useRef<HTMLCanvasElement>(null);
  const [doc, setDoc] = useState<pdfjs.PDFDocumentProxy | null>(null);
  const [errore, setErrore] = useState<string | null>(null);

  useEffect(() => {
    let vivo = true;
    let compito: pdfjs.PDFDocumentLoadingTask | null = null;
    file.arrayBuffer()
      .then((b) => { compito = pdfjs.getDocument({ data: new Uint8Array(b) }); return compito.promise; })
      .then((d) => { if (vivo) { setDoc(d); onPagine?.(d.numPages); } })
      .catch((e) => vivo && setErrore(String(e)));
    return () => { vivo = false; void (compito as pdfjs.PDFDocumentLoadingTask | null)?.destroy(); };
  }, [file, onPagine]);

  useEffect(() => {
    if (!doc || !tela.current) return;
    let compito: pdfjs.RenderTask | null = null;
    let vivo = true;
    doc.getPage(Math.min(pagina, doc.numPages)).then((p) => {
      if (!vivo || !tela.current) return;
      const vista = p.getViewport({ scale: 1.6 });
      const c = tela.current;
      c.width = vista.width;
      c.height = vista.height;
      compito = p.render({ canvas: c, viewport: vista });
      compito.promise.catch(() => undefined);
    });
    return () => { vivo = false; compito?.cancel(); };
  }, [doc, pagina]);

  if (errore) return <p className="errore">PDF non leggibile nel browser: {errore}</p>;
  return <canvas ref={tela} aria-label={`Pagina ${pagina} del documento originale`} />;
}

export default function VistaOriginale({ file, pagina }: { file: File; pagina: number }) {
  const [url, setUrl] = useState<string | null>(null);
  const [testo, setTesto] = useState<string | null>(null);
  const pdf = file.type === "application/pdf" || /\.pdf$/i.test(file.name);
  const immagine = IMMAGINI_BROWSER.test(file.name);

  useEffect(() => {
    if (!immagine) return;
    const u = URL.createObjectURL(file);
    setUrl(u);
    return () => URL.revokeObjectURL(u);
  }, [file, immagine]);
  useEffect(() => {
    if (!pdf && !immagine && !/\.tiff?$/i.test(file.name)) file.text().then(setTesto);
  }, [file, pdf, immagine]);

  if (pdf) return <VistaPdf file={file} pagina={pagina} />;
  if (immagine && url) return <img src={url} alt="Documento originale" />;
  if (/\.tiff?$/i.test(file.name))
    return <p className="tenue">Il browser non mostra i file TIFF: confronta con il Markdown in chiaro.</p>;
  return <pre className="grezzo">{testo}</pre>;
}
