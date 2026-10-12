// Download di un Blob dal browser. L'ancora va nel DOM (Firefox ignora il click altrimenti) e l'URL si
// revoca solo dopo che il browser ha avviato il download (una revoca immediata annulla il file).

/** Avvia il download di `blob` con il nome `nome`. */
export function scaricaBlob(blob: Blob, nome: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = nome;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}
