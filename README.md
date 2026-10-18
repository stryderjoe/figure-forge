# Figure Forge

Build your own characters from TT's LEGO games (PC), in three modes, one per era of their file formats:

- **Classic (2007 to 2008)**: LEGO Star Wars: The Complete Saga, LEGO Indiana Jones: The Original Adventures,
  LEGO Batman: The Videogame;
- **Super-hero era (2011 to 2013)**: LEGO Pirates of the Caribbean, LEGO Batman 2: DC Super Heroes, LEGO Marvel
  Super Heroes;
- **Beyond Gotham era (2014)**: LEGO Batman 3: Beyond Gotham.

Parts and animations move between all of them.

1. **Body**: pick the character whose skeleton your figure is built on.
2. **Parts**: swap heads, hair, hats, faces, torsos, arms, hands, hips, legs, capes, back items and held
   items from any character of any of the games. Upload your own parts too: OBJ, glTF/GLB, STL, FBX or
   LDraw (`.ldr`, `.mpd`, `.dat`).
3. **Colours**: recolour any surface with the LEGO palette or any colour.
4. **Prints**: put your own pictures on torsos, faces, legs and any other surface; drag, scale, turn and
   mirror them over the part's UV layout until they fit.
5. **Render**: the classic games' shiny-plastic look, a studio look, or flat textures; save a PNG.
6. **Rig**: the figure is rigged on the game skeleton with the games' own skin weights; uploaded parts ride
   on a bone or are skinned with the body's own game weights (each point takes the weights of the body's
   nearest points, so it bends at the joints as the body does).
7. **Animate**: play any character's own animations on your figure. Clips from another character, even
   from another game, are retargeted to your body. In LEGO Batman 2, Marvel and LEGO Batman 3 the faces move
   with them (the games' facial blend shapes and each clip's face track).
8. **Export**: GLB or glTF (rigged, with the playing animation), FBX, Blender `.blend` and USD (through
   Blender), OBJ with its materials and textures, STL, PLY, PNG, and a project file you can open again.
   **Into the game**: put the figure into its body's game, replacing that character or as a new one.

By **Gibby**, MIT licence. Not affiliated with TT Games, Warner Bros. Games, Lucasfilm, Disney, Marvel, DC or the LEGO Group.

## No game files included

Figure Forge reads the files of **your own copy** of each game, extracted from its `.DAT` archives, and never
changes them. It includes no game models, textures or animations, and nothing it makes from your files
leaves your computer: the program is a small web server on `127.0.0.1` with its own window. Do not share
exports that contain the games' assets.

To extract a game, use an archive extractor, such as
[TTGames-Workshop](https://github.com/Loomirr/TTGames-Workshop)'s archive tools or `tools/extract_game.py`
in [tt-oldera-tools](https://github.com/stryderjoe/tt-oldera-tools). Keep the folder layout: the folder you
give Figure Forge is the one holding `chars` (and `levels`, `cut` ...).

## Run it

**Windows build:** unzip and run `FigureForge.exe`. Open **Settings** (the gear) and give the extracted
folder of each game you have; one is enough.

**From source** (Python 3.10 or newer):

    python -m pip install -r requirements.txt
    python run.py              # its own window (pywebview, using Microsoft Edge WebView2)
    python run.py --browser    # in your web browser instead
    python run.py --serve      # only the server; prints the address

Optional: [Blender](https://www.blender.org) 4.2 or newer for FBX, `.blend` and USD export (found in Program
Files, or set its path in Settings), and the [LDraw parts library](https://library.ldraw.org) for LDraw
models that name parts by number. Exports go to `Documents\Figure Forge`.

Build the Windows folder yourself with `python -m PyInstaller FigureForge.spec --noconfirm`.

## How it works

The classic games share one model format (NU20 `.ghg`) and one animation format (`.an3`). A character
model holds a skeleton and **layers** (face, head, hair, mask, body colours, capes, gear ...); its `.txt`
says which layers the game shows and which animations it uses. Figure Forge:

- splits every character into parts: rigid pieces by their bone and layer, skinned bodies by each
  triangle's strongest bone (torso, arms, hands, hips, legs);
- gives every bone a common name. All the humanoids of the three games use one of two naming schemes
  (`Hips`, `LeftUpLeg` ... or `upperTorso`, `leftLeg` ...) with the same structure, so parts and clips move
  between them;
- fits a part to your body: a rigid piece keeps its shape and orientation on the matching bone; a skinned
  piece is re-bound so each vertex follows your body's bones as it followed its own;
- plays clips by each game's rule. LEGO Batman and Indiana Jones store a bone's whole rotation; Star Wars
  stores it on top of the bone's rest rotation. Both rules were measured by skinning each game's meshes and
  keeping the rule under which they stretch least.

## Checks

    python tests/check_api.py <lb1 folder> <indy folder> <tcs folder>
    python tests/check_ui.py <out folder> <lb1 folder> <indy folder> <tcs folder>
    blender -b --factory-startup --python tests/check_exports_blender.py -- <out folder>/exports test_figure

| Check | Result |
| --- | --- |
| index every character of the three games (LEGO Batman 292, Indiana Jones 210, Star Wars 308 models) | no errors; 74 + 139 + 144 bodies; 8,344 parts |
| a body from each game with its own parts, textures and clips | all three |
| parts across games and skeleton schemes (Luke's head and hair, Indy's hat, on Batman) | fitted |
| a clip retargeted onto its own skeleton | equal to the clip (largest difference 1e-6) |
| the user interface in headless Microsoft Edge: every step, every export, saving and reopening a project | all passed |
| exports opened in Blender: GLB, glTF, FBX, `.blend` (one armature, skinned meshes, moving bones); STL, PLY, OBJ | all passed |
| the Windows build, started as a server | serves the page and the games |
| the newer games' faces (`tests/check_faces.py`): LEGO Batman 2's Joker, Marvel's Hulk, LEGO Batman 3's Joker | each face's blend shapes load, a clip's face track moves them, the face changes between frames, a GLB keeps shapes and weights |
| a figure put into copies of LEGO Batman 2's, LEGO Marvel's and LEGO Pirates' GAME.DAT (`tests/check_nxg_export.py`), read back | the body wears it; every other file untouched; Restore gives the archive back |

## Limits

- Faces move in LEGO Batman 2, LEGO Marvel and LEGO Batman 3 (blend shapes driven by each clip's face track).
  The classic games print their faces and do not move them in play; their cutscenes' expression track is not
  read. LEGO Pirates' clips carry no face track.
- Put into LEGO Marvel or LEGO Batman 3, a figure's face (the newer games' face pieces) is written without its
  mouth mask, decal settings or blend shapes, and in play it can look blank or scrambled.
- Figures go into LEGO Batman, Indiana Jones, Star Wars, LEGO Batman 2, LEGO Marvel Super Heroes, LEGO
  Pirates and LEGO Batman 3 (replace a character or add one). The LEGO Batman 2, Marvel, Pirates and LEGO
  Batman 3 exports are checked by reading the archives back (LEGO Batman 3's load lists are written the way
  its build writes them, checked on all 4563 of the game's own). Replacing a character has been tried in
  the running games: the figure shows, animates and plays in each one's first level (LEGO Batman 2 as
  Batman, Marvel as Iron Man, Pirates as Will Turner, LEGO Batman 3 as Batman). In these games the figure
  takes the place of the body template image rather than adding a texture.
- **Portraits:** every export draws the figure's portrait (the head, front on, as the games draw theirs) and
  puts it where each game keeps its portraits: the classic games' icon scenes, the newer games' icon images
  and packs, Marvel's page of portraits and LEGO Batman 3's sheet. Faces with a mouth mask are drawn with the
  mouth closed, as the games show them. A new character gets a portrait of its own in every game but LEGO
  Pirates, which leaves a new character out of the game when it has a full-size portrait of its own (in a slot
  of its own or in a cut character's): its new characters show their body's portrait. Seen in the running
  games: LEGO Batman 2008, LEGO Batman 2, Star Wars and Indiana Jones (replace and new), LEGO Batman 3 (new),
  Pirates (replace). LEGO Marvel and LEGO Batman 3 draw the corner portrait in play from the figure itself
  (seen: it shows the figure's head); their portrait files are the games' other pictures of the character.
- **New characters** are registered where the games look for them: the character lists, `hgopathnames.txt`
  and the character projects (`.apj` / `.cpj`). Seen in the running games as a level's player (made so
  through the level's text; that step is not made by Figure Forge yet): all seven games.
- LEGO Batman 3: Beyond Gotham has its own mode (its files are newer versions of the super-hero era's). Its
  figures, prints, colours, faces, capes and animations are read (and its faces' expression data, which the
  viewer does not use yet for the newer games).
- A part fitted from a very different body (a droid's torso on a minifig) keeps its own shape and may need a
  touch of rigging.
- Collada (`.dae`) export needs a Blender version that still has it.

## Credits and licence

By **Gibby** (stryderjoe), with AI assistance (Claude). The format research is shared with
[TTGames-Workshop](https://github.com/Loomirr/TTGames-Workshop) by Loomirr; no TTGames-Workshop code is
included. Uses [three.js](https://threejs.org) (MIT, in `ui/vendor/three`), pywebview (BSD), NumPy (BSD)
and Pillow (MIT-CMU). MIT; see [LICENSE](LICENSE).
