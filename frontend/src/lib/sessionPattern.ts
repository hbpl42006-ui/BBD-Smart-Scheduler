export type SessionPatternParse = { values:number[]; error?:string };

export function parseSessionPattern(value:string):SessionPatternParse {
  const parts=value.split(/[+,]/).map(x=>x.trim());
  if (!value.trim() || parts.some(x=>!/^\d+$/.test(x) || Number(x)<1)) {
    return {values:[],error:'Use positive whole numbers separated by + or commas, e.g. 1 + 1 + 1.'};
  }
  return {values:parts.map(Number)};
}

export function validateSessionPattern(required:number, values:number[]) {
  const total=values.reduce((sum,x)=>sum+x,0);
  return {valid:values.length>0 && total===required,total};
}
