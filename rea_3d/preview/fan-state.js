export const SAVE_KEY='rea3d.fan.newleaf.v1';
export const DEMO_PARTY=Object.freeze({speciesId:1,name:'이상해씨',englishName:'Bulbasaur',level:15,hp:41,maxHp:41,types:['풀','독'],moves:[{name:'몸통박치기',original:'Tackle'},{name:'울음소리',original:'Growl'},{name:'덩굴채찍',original:'Vine Whip'},{name:'수면가루',original:'Sleep Powder'}],source:'local demo party based on the isolated original-game test fixture'});
export function defaultState(){return {version:1,trainerName:'새잎',settings:{autoRotate:false,animation:true,clipIndex:0,eyeValue:0},savedAt:null};}
export function validateSave(value,primaryIndices){
  if(!value||value.version!==1||typeof value.trainerName!=='string'||!value.trainerName.trim()||value.trainerName.length>12)throw new Error('저장된 트레이너 정보가 올바르지 않아요.');
  const settings=value.settings;
  if(!settings||typeof settings.autoRotate!=='boolean'||typeof settings.animation!=='boolean'||!Number.isInteger(settings.clipIndex)||!primaryIndices.includes(settings.clipIndex)||![0,1,2].includes(settings.eyeValue??0))throw new Error('이 모델에 맞지 않는 저장 설정이에요.');
  if(typeof value.savedAt!=='string'||!Number.isFinite(Date.parse(value.savedAt)))throw new Error('저장 시간을 확인할 수 없어요.');
  return {version:1,trainerName:value.trainerName.trim(),settings:{autoRotate:settings.autoRotate,animation:settings.animation,clipIndex:settings.clipIndex,eyeValue:settings.eyeValue??0},savedAt:value.savedAt};
}
