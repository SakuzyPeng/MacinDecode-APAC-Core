"""Independent full-source scene descriptions and passive label variants."""
from spectrum_vectors import bits

def extension(entries):
    if not entries:return '0'
    assert len(entries)<=7;wire='1'
    for e in entries:
        data=e['bits'];width=max(4,(len(data)-1).bit_length())
        wire+=bits(e['type'],3)+bits(width-4,4)+bits(len(data)-1,width)+data
    return wire+'000'

def tag(spec):
    if isinstance(spec,int):spec=dict(words=[spec])
    words=spec.get('words',[21]);wire=bits(len(words)-1,2)+''.join(bits(v,10) for v in words)
    fallback=spec.get('fallback');wire+=bits(fallback is not None,1)
    if fallback is not None:wire+=bits(len(fallback)-1,2)+''.join(bits(v,10) for v in fallback)
    return wire+extension(spec.get('extensions',[]))

def language(text,flag=False):
    alphabet='abcdefghiklmnoprstuvwyz';wire=''
    for c in text:
        if c=='-':wire+='001'
        elif c in alphabet:wire+=bits(8+alphabet.index(c),5)
        elif c in 'jqx':wire+=bits(248+'jqx'.index(c),8)
        elif c in '0123456789':wire+=bits(502+int(c),9)
        else:raise ValueError('language character outside prefix alphabet')
    return wire+'000'+bits(flag,1)

def controls(spec=None):
    spec=spec or {};wire=bits(spec.get('primary',False),1);ranges=spec.get('ranges',{})
    for i,width in enumerate((7,5,5,3)):
        present=i in ranges or str(i) in ranges;wire+=bits(present,1)
        if present:
            values=ranges.get(i,ranges.get(str(i)))
            if i==0:wire+=bits(values is not None,1)
            if values is not None:wire+=''.join(bits(v,width) for v in values)
    wire+=bits(spec.get('secondary',False),1)
    present=spec.get('parameters',True);wire+=bits(present,1)
    if present or spec.get('primary',False):
        wire+=bits(spec.get('parameter_0',0),6)
        for i,width in enumerate((9,6,6,4,1),1):
            value=spec.get('parameter_'+str(i));wire+=bits(value is not None,1)
            if value is not None:wire+=bits(value,width)
    return wire

def encode(count,drc=False,spec=None):
    spec=spec or {};items=spec.get('items',[list(range(count))]);groups=spec.get('groups',[list(range(len(items)))]);presets=spec.get('presets',[{}])
    language_items=spec.get('language_items',[]);selection_items=spec.get('selection_items',[])
    flags=spec.get('flags',[drc,False,False]);wire=''.join(bits(v,1) for v in flags)+bits(spec.get('parameter',0),2)+bits(1,6)+'1'
    text=spec.get('language');wire+=bits(text is not None,1)
    if text is not None:wire+=language(text)
    wire+=bits(len(items),6)+bits(len(language_items),6)+bits(len(selection_items),6)+bits(len(groups),5)+bits(len(presets),4)
    item_tag=spec.get('item_tag',21)
    for item in items:
        wire+=bits(len(item),6)+''.join(bits(i,6) for i in item)+bits(item_tag is not None,1)
        if item_tag is not None:wire+=tag(item_tag)
    for item in language_items:
        sources=item['sources'];wire+=bits(len(sources),2)+''.join(bits(i,6) for i in sources)+language(item.get('language',''))+bits(item.get('flag_a',False),1)+bits(item.get('flag_b',False),1)
    for item in selection_items:
        refs=item['items'];wire+=bits(len(refs),6)+''.join(bits(i,6) for i in refs)+bits(item.get('parameter',0),15)
        label=item.get('tag');wire+=bits(label is not None,1)
        if label is not None:wire+=tag(label)
    group_tag=spec.get('group_tag',21)
    for group in groups:
        selections=[] if isinstance(group,list) else group.get('selections',[])
        members=group if isinstance(group,list) else group.get('items',[])
        wire+=bits(len(members),6)+''.join(bits(i,6) for i in members)+bits(len(selections),6)+''.join(bits(i,6) for i in selections)
        wire+=''.join(controls(preset.get('controls',spec.get('controls'))) for preset in presets)
        wire+=bits(group_tag is not None,1)
        if group_tag is not None:wire+=tag(group_tag)
        wire+=extension(spec.get('group_extensions',[]))
    selection_data=spec.get('preset_selection_data',False);wire+=bits(selection_data,1)
    for preset in presets:
        chars=preset.get('characteristics',[False]*13+[True]*3);index={0:0,16:1,64:2,256:3}[len(chars)]
        wire+=tag(preset.get('tag',97))+bits(index,2)+''.join(bits(v,1) for v in chars)+language(preset.get('language',''),preset.get('language_flag',False))
        if selection_data:
            refs=preset.get('selection_indices',[]);wire+=bits(preset.get('selection_flag',False),1)+bits(len(refs),6)+''.join(bits(v,6) for v in refs)
    text=spec.get('tag');wire+=bits(text is not None,1)
    if text is not None:wire+=tag(text)
    categories=spec.get('categories');wire+=bits(categories is not None,1)
    if categories is not None:
        wire+=bits(len(categories),4)
        for category in categories:
            refs=category['groups'];wire+=bits(len(refs),6)+''.join(bits(v,6) for v in refs)
            members=category.get('members');wire+=bits(members is not None,1)
            if members is not None:
                assert len(members)==len(presets);wire+=''.join(bits(v,6) for v in members)
            label=category.get('tag');wire+=bits(label is not None,1)
            if label is not None:wire+=tag(label)
    wire+=extension(spec.get('extensions',[]))
    text=spec.get('root_tag');wire+=bits(text is not None,1)
    if text is not None:wire+=tag(text)
    return wire+extension(spec.get('root_extensions',[]))

def variants(count):
    yield 'default',{}
    yield 'tag-words',dict(item_tag=dict(words=[0,1023,21,5],fallback=[20,21]),group_tag=dict(words=[5,17]),tag=7,root_tag=21)
    yield 'untagged',dict(item_tag=None,group_tag=None)
    yield 'language',dict(language='en-us',presets=[dict(language='ja-jp')])
    yield 'no-parameters',dict(controls=dict(parameters=False))
    yield 'split-items',dict(items=[[i] for i in range(count)])
    yield 'split-groups',dict(items=[[i] for i in range(count)],groups=[[i] for i in range(count)])
    yield 'reverse-groups',dict(items=[[i] for i in range(count)],groups=[[i] for i in reversed(range(count))])
    yield 'reverse-sources',dict(items=[list(reversed(range(count)))])
    yield 'two-presets',dict(presets=[{},dict(tag=98,language='en')])
    yield 'characteristics-empty',dict(presets=[dict(characteristics=[])])
    yield 'characteristics-wide',dict(presets=[dict(characteristics=[False]*64)])
    ext=[dict(type=1,bits='101'),dict(type=7,bits='0'*35)]
    yield 'extensions',dict(item_tag=dict(words=[21],extensions=ext),group_extensions=ext,extensions=ext,root_extensions=ext)
    yield 'preset-selection-empty',dict(preset_selection_data=True)
    yield 'preset-selection-explicit',dict(preset_selection_data=True,presets=[dict(selection_flag=True,selection_indices=[0])])
    for flag in (False,True):
        for index in range(count):
            yield f'preset-selection-split-{int(flag)}-{index}',dict(items=[[i] for i in range(count)],groups=[[i] for i in range(count)],preset_selection_data=True,presets=[dict(selection_flag=flag,selection_indices=[index])])
    yield 'categories-empty',dict(categories=[])
    yield 'category-one',dict(categories=[dict(groups=[0],members=[0],tag=21)])
    yield 'category-split',dict(items=[[i] for i in range(count)],groups=[[i] for i in range(count)],categories=[dict(groups=list(range(count)),members=[0])])
    languages=[dict(sources=list(range(count)),language='en',flag_a=True),dict(sources=list(range(count)),language='ja')]
    selections=[dict(items=[0,1],parameter=32767,tag=21)]
    yield 'inactive-languages',dict(language_items=languages,selection_items=selections)
    yield 'unused-language-alternative',dict(language_items=languages,selection_items=selections,groups=[dict(items=[0],selections=[0])])
    yield 'equivalent-languages',dict(items=[],language_items=languages,selection_items=selections,groups=[dict(selections=[0])])
    yield 'different-languages',dict(items=[],language_items=[dict(sources=[0],language='en',flag_a=True),dict(sources=[count-1],language='ja')],selection_items=selections,groups=[dict(selections=[0])])
    for parameter,values in [(1,(0,255,256,257,511)),(2,(0,31,63)),(3,(0,31,63)),(4,(0,7,15)),(5,(0,1))]:
        for value in values:yield f'parameter-{parameter}-{value}',dict(controls={'parameter_'+str(parameter):value})
    for ranges in ({0:None},{0:(0,127)},{0:(40,80)},{1:(0,31)},{2:(0,31)},{3:(0,7)}):
        i=next(iter(ranges));suffix='default' if ranges[i] is None else '-'.join(map(str,ranges[i]));yield f'range-{i}-{suffix}',dict(controls=dict(ranges=ranges))
    for primary in (False,True):
        for secondary in (False,True):
            for present in (False,True):
                yield f'controls-{int(primary)}-{int(secondary)}-{int(present)}',dict(controls=dict(primary=primary,secondary=secondary,parameters=present))
    for flags in range(8):
        for parameter in range(4):yield f'flags-{flags}-{parameter}',dict(flags=[bool(flags&(1<<i)) for i in range(3)],parameter=parameter)
