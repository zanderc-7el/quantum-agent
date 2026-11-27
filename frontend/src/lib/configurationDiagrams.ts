export interface DiagramImage { name: string; dataUrl: string; width: number; height: number; source?: string; date?: string; }
export interface TopologyNode { resourceId: string; designId?: string; x: number; y: number; }
export interface TopologyEdge { id: string; from: string; to: string; kind: string; confirmation: string; source: string; }
export interface ChipTopologyData { nodes: readonly TopologyNode[]; edges: readonly TopologyEdge[]; source?: string; date?: string; image?: DiagramImage; }
export interface SignalComponent { id: string; type: string; label: string; channel?: string; port?: string; fromPort?: string; toPort?: string; temperatureK?: number; model?: string; attenuationDb?: number; delayNs?: number; source?: string; confirmation?: string; }
export interface SignalPath { id: string; label: string; direction: string; confirmation: string; source: string; station?: string; sourceDate?: string; image?: DiagramImage; components: SignalComponent[]; }
export const MAX_DIAGRAM_IMAGE_BYTES = 512 * 1024;
export const MAX_DIAGRAM_DATA_URL_CHARS = 700 * 1024;
export const isRasterImage = (image?: DiagramImage): image is DiagramImage => !!image && typeof image.name==='string' && !!image.name.trim() && image.name.length<=500 && typeof image.dataUrl==='string' && /^data:image\/(png|jpeg);base64,[A-Za-z0-9+/]+={0,2}$/.test(image.dataUrl) && image.dataUrl.length <= MAX_DIAGRAM_DATA_URL_CHARS && Number.isInteger(image.width) && Number.isInteger(image.height) && image.width > 0 && image.height > 0 && image.width <= 8192 && image.height <= 8192 && image.width * image.height <= 16000000 && (['source','date'] as const).every(key=>image[key]===undefined||(typeof image[key]==='string'&&!!image[key].trim()&&image[key].length<=2000));
export async function readDiagramImage(file: File): Promise<DiagramImage> {
  if (!['image/png', 'image/jpeg'].includes(file.type)) throw new Error('仅支持 PNG 或 JPEG 原图，不支持 SVG。');
  if (!file.size || file.size > MAX_DIAGRAM_IMAGE_BYTES) throw new Error('原图须小于 512 KiB，请先导出较小的 PNG/JPEG，再上传；不会自动覆盖原始文件。');
  const dataUrl = await new Promise<string>((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result));reader.onerror=()=>reject(new Error('图片读取失败'));reader.readAsDataURL(file);});
  const dimensions = await new Promise<{width:number;height:number}>((resolve,reject)=>{const image=new Image();image.onload=()=>resolve({width:image.naturalWidth,height:image.naturalHeight});image.onerror=()=>reject(new Error('文件无法解码为 PNG/JPEG 图片'));image.src=dataUrl;});
  const image={name:file.name,dataUrl,...dimensions};
  if(!isRasterImage(image))throw new Error('图片最大边长为 8192 像素，总像素不超过 1600 万。');
  return image;
}
export function topologySelection(topology: ChipTopologyData | undefined, scope: string, selected?: { scope: string; resourceId: string }) {
  const resourceId = selected?.scope === scope && topology?.nodes?.some(n => n.resourceId === selected.resourceId) ? selected.resourceId : undefined;
  const edges = resourceId ? (topology?.edges ?? []).filter(e => (e.from === resourceId || e.to === resourceId) && topology?.nodes.some(n => n.resourceId === e.from) && topology?.nodes.some(n => n.resourceId === e.to)) : [];
  const neighbors = [...new Set(edges.filter(e => e.confirmation === 'confirmed' && !!e.source?.trim() && e.kind === 'coupling').map(e => e.from === resourceId ? e.to : e.from))];
  return { resourceId, edges, neighbors };
}
