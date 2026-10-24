import test from 'node:test';
import assert from 'node:assert/strict';
import {defaultState,validateSave,DEMO_PARTY,SAVE_KEY} from '../preview/fan-state.js';

test('fan local state restore accepts only the current model clips and restores a bounded copy',()=>{
  const source={...defaultState(),trainerName:'  새잎  ',savedAt:'2026-10-23T13:00:00.000Z'};
  const restored=validateSave(source,[0,11]);assert.equal(restored.trainerName,'새잎');assert.notEqual(restored.settings,source.settings);
  for(const patch of [{version:2},{trainerName:''},{trainerName:'x'.repeat(13)},{savedAt:'bad'},{settings:{...source.settings,clipIndex:29}},{settings:{...source.settings,eyeValue:3}},{settings:{...source.settings,animation:'yes'}}])assert.throws(()=>validateSave({...source,...patch},[0,11]));
  assert.equal(source.trainerName,'  새잎  ');assert.equal(SAVE_KEY,'rea3d.fan.newleaf.v1');
});
test('fan demo party remains explicit and matches the isolated Bulbasaur fixture',()=>{
  assert.equal(DEMO_PARTY.speciesId,1);assert.equal(DEMO_PARTY.level,15);assert.equal(DEMO_PARTY.hp,41);assert.equal(DEMO_PARTY.maxHp,41);assert.equal(DEMO_PARTY.moves.length,4);assert.match(DEMO_PARTY.source,/isolated original-game test fixture/);
  const one=defaultState(),two=defaultState();one.settings.animation=false;assert.equal(two.settings.animation,true);
});
