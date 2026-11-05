#define private public
#define protected public
#include "../worldgen/third_party/robin_hood.h"
#include <cstdio>
struct K{int x,y; bool operator==(const K&o)const{return x==o.x&&y==o.y;}};
static uint64_t hi(int v){uint64_t x=(uint64_t)(int64_t)v; x=((x>>33)^x)*0xFF51AFD7ED558CCDull; return (x>>33)^x;}
struct H{ size_t operator()(const K&k)const{ uint64_t h1=hi(k.x),h2=hi(k.y); uint64_t c=h1+0x9E3779B9ull; c=(h2+h1*0x40ull+0x282C15E7F9ull+(c>>2))^c; uint64_t d=((c>>33)^c)*0xFF51AFD7ED558CCDull; return (d>>33)^d; } };
int main(){ int n; robin_hood::unordered_flat_map<K,int,H> m;
 scanf("%d",&n); for(int i=0;i<n;i++){K k; scanf("%d %d",&k.x,&k.y); m.emplace(k,i);}
 size_t nb=m.calcNumElementsWithBuffer(m.mMask+1);
 printf("mask %zu inc %u shift %u max %zu mul %llx\n",m.mMask,m.mInfoInc,m.mInfoHashShift,m.mMaxNumElementsAllowed,(unsigned long long)m.mHashMultiplier);
 for(size_t i=0;i<nb;i++) if(m.mInfo[i]) printf("%zu:%d/%u ",i,m.mKeyVals[i].getSecond(),m.mInfo[i]); printf("\n"); }
