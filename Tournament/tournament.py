import os
import xml.etree.ElementTree as ET
import random
import threading
import time
import discord
import asyncio
import warnings
from datetime import datetime

from typing import List
from typing import Tuple

from .tournamentUtils import *
from .match import match
from .player import player
from .deck import deck


"""
    This is a tournament class. The bulk of data management for a tournament is handled by this class.
    It also holds certain metadata about the tournament, such as the tournament's name and host guild's name.

    These are the current functionalities that this class has (those they might not have an explicit method):
        - Tracks players, matches, and the status of the tournament (state of registeration, whether or not the tournament has started, etc.)
        - Matches can be added
        - The results for a match can be recorded and verified

    Things that will be added in the future:
        - 
    
    The class has the following member variables:
        - tournName: The name of the tournament
        - hostGuildName: The name of the server that is hosting the tournament
        - format: The format of the tournament
        - regOpen: Whether or not registeration is open
        - tournStarted: Whether or not the tournament has started
        - tournEnded: Whether or not the tournament has ended
        - tournCancel: Whether or not the tournament has been canceled
        - playersPerMatch: The number of players that will be paired per match
        - queue: A list of player names (strings) representing the players that are waiting to be paired for a match
        - activePlayers: A dict that index-s player objects that haven't dropped with their names (for ease of referencing)
        - droppedPlayers: A dict that index-s player objects that have dropped with their names (for ease of referencing)
        - matches: A list of all match objects in the tournament, regardless of status
"""
class tournament:
    def __init__( self, a_tournName: str, a_hostGuildName: str, a_format: str = "EDH" ):
        self.tournName = a_tournName
        self.hostGuildName = a_hostGuildName
        self.format    = a_format
        
        self.guild   = ""
        self.guildID = ""
        self.role    = ""
        self.roleID  = ""
        self.pairingsChannel = ""
        
        self.regOpen      = True
        self.tournStarted = False
        self.tournEnded   = False
        self.tournCancel  = False
        
        self.loop = asyncio.new_event_loop()
        self.fail_count = 0
        
        self.queue             = [ [] ]
        self.playersPerMatch   = 4
        self.pairingsThreshold = self.playersPerMatch * 2 + 3
        self.pairingWaitTime   = 15
        self.queueActivity     = [ ]
        self.highestPriority   = 0
        self.pairingsThread    = threading.Thread( target=self.launch_pairings )
        
        self.deckCount = 1

        self.activePlayers  = {}
        self.droppedPlayers = {}
        
        self.matches = []
    
    def isPlanned( self ) -> bool:
        return not ( self.tournStarted or self.tournEnded or self.tournCancel )
    
    def isActive( self ) -> bool:
        return self.tournStarted and not ( self.tournEnded or self.tournCancel )
    
    def isDead( self ) -> bool:
        return self.tournEnded or self.tournCancel
    
    def addDiscordGuild( self, a_guild ) -> None:
        self.guild = a_guild
        self.hostGuildName = a_guild.name
        self.guildID = self.guild.id
        if self.roleID != "":
            self.role = a_guild.get_role( self.roleID )
        else:
            self.role = discord.utils.get( a_guild.roles, name=f'{self.tournName} Player' )
        self.pairingsChannel = discord.utils.get( a_guild.channels, name="match-pairings" )
    
    # The name of players ought to be their Discord name + discriminator
    def assignGuild( self, a_guild ) -> None:
        print( f'The guild "{a_guild}" is being assigned to {self.tournName}.' )
        print( f'There are {len(a_guild.members)} members in this guild.' )
        self.addDiscordGuild( a_guild )
        for player in self.activePlayers:
            ID = self.activePlayers[player].discordID
            if ID != "":
                self.activePlayers[player].addDiscordUser( self.guild.get_member( ID ) )
        for match in self.matches:
            if match.roleID != "":
                match.addMatchRole( a_guild.get_role( match.roleID ) )
            if match.VC_ID != "":
                match.addMatchVC( a_guild.get_channel( match.VC_ID ) )

    def setRegStatus( self, a_status: bool ) -> str:
        if not ( self.tournEnded or self.tournCancel ):
            self.regOpen = a_status
            return ""
        elif self.tournEnded:
            return "This tournament has already ended. As such, registeration can't be opened."
        elif self.tournCancel:
            return "This tournament has been cancelled. As such, registeration can't be opened."
    
    def startTourn( self ) -> str:
        if not (self.tournStarted or self.tournEnded or self.tournCancel):
            self.tournStarted = True
            self.regOpen = False
            return ""
        elif self.tournEnded:
            return "This tournament has already ended. As such, it can't be started."
        elif self.tournCancel:
            return "This tournament has been cancelled. As such, it can't be started."
    
    async def purgeTourn( self ) -> None:
        for match in self.matches:
            if type( match.VC ) == discord.VoiceChannel:
                try:
                    await match.VC.delete( )
                except:
                    pass
            if type( match.role ) == discord.Role:
                try:
                    await match.role.delete( )
                except:
                    pass
        if type( self.role ) == discord.Role:
            try:
                await self.role.delete( )
            except:
                pass
    
    async def endTourn( self ) -> str:
        await self.purgeTourn( )
        if not self.tournStarted:
            return "The tournament has not started. As such, it can't be ended; however, it can be cancelled. Please use the cancel command if that's what you intended to do."
        else:
            self.tournEnded = False
    
    async def cancelTourn( self ) -> str:
        await self.purgeTourn( )
        self.tournCancel = True
        return "This tournament has been canceled."
    
    def addPlayer( self, a_discordUser ) -> str:
        if self.tournCancel:
            return "Sorry but this tournament has been cancelled. If you believe this to be incorrect, please contact the tournament officials."
        if self.tournEnded:
            return "Sorry, but this tournament has already ended. If you believe this to be incorrect, please contact the tournament officials."
        if self.regOpen:
            self.activePlayers[getUserIdent(a_discordUser)] = player( getUserIdent(a_discordUser) )
            self.activePlayers[getUserIdent(a_discordUser)].addDiscordUser( a_discordUser )
            return ""
        else:
            return "Sorry, registeration for this tournament isn't open currently."
    
    # There will be a far more sofisticated pairing system in the future. Right now, the dummy version will have to do for testing
    # This is a prime canidate for adjustments when players how copies of match results.
    def addPlayerToQueue( self, a_player: str ) -> None:
        for lvl in self.queue:
            if a_player in lvl:
                return "You are already in the matchmaking queue."
        if a_player not in self.activePlayers:
            return "You aren't an active player."
        
        self.queue[0].append(self.activePlayers[a_player])
        self.queueActivity.append( (a_player, datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S.%f') ) )
        time.sleep( 10**-6 )
        print( f'Added {a_player} to the queue' )
        if sum( [ len(level) for level in self.queue ] ) > self.pairingsThreshold and not (self.loop.is_running( ) or self.pairingsThread.is_alive()):
            print( "Creating task" )
            self.pairingsThread = threading.Thread( target=self.launch_pairings )
            self.pairingsThread.start( )
    
    async def addMatch( self, a_players: List[str] ) -> None:
        for plyr in a_players:
            self.queueActivity.append( (plyr, datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S.%f') ) )
        newMatch = match( a_players )
        self.matches.append( newMatch )
        newMatch.matchNumber = len(self.matches)
        if type( self.guild ) == discord.Guild:
            matchRole = await self.guild.create_role( name=f'Match {newMatch.matchNumber}' )
            overwrites = { self.guild.default_role: discord.PermissionOverwrite(read_messages=False),
                           getAdminRole(self.guild): discord.PermissionOverwrite(read_messages=True),
                           matchRole: discord.PermissionOverwrite(read_messages=True) }
            newMatch.VC = await self.guild.create_voice_channel( name=f'Match {newMatch.matchNumber}', overwrites=overwrites, category=discord.utils.get( self.guild.categories, name="Matches" ) ) 
            newMatch.role = matchRole
            message  = f'{matchRole.mention}, you have been paired for your match. There is a voice channel for you that you may join. Below in information about your opponents.\n'
        
        for plyr in a_players:
            self.activePlayers[plyr].matches.append( newMatch )
            for p in a_players:
                if p != plyr:
                    self.activePlayers[plyr].opponents.append( p )
            if type( self.guild ) == discord.Guild:
                await self.activePlayers[plyr].discordUser.add_roles( matchRole )
                message += f'{self.activePlayers[plyr].pairingString()}\n' 
        
        if type( self.guild ) == discord.Guild:
            await self.pairingsChannel.send( message )
    
    # A wrapper for run_pairings to prevent "RuntimeError('This event loop is already running')"
    def launch_pairings( self ):
        # print( f'Inside launch_pairings. This thread ID is {threading.get_ident()} while the newest Thread ID is {self.pairingsThread.ident}' )
        while self.loop.is_running():
            if threading.get_ident() != self.pairingsThread.ident:
                # print( f'Aborting launch_pairings. This thread ID is {threading.get_ident()} while the newest Thread ID is {self.pairingsThread.ident}' )
                return
            pass
        t = threading.Thread( target=self.run_pairings )
        t.start( )

    # Wrapper for self.pairQueue so that it can be ran on a seperate thread
    def run_pairings( self ):
        try:
            self.loop.run_until_complete( self.pairQueue( ) )
        except RuntimeWarning:
            self.fail_count += 1
    
    # Starting from the given position, searches through the queue to find opponents for the player at the given position.
    # Returns the positions of the closest players that can form a match.
    # If a match can't be formed, we return an empty list
    # This method is intended to only be used in pairQueue method
    def searchForOpponents( self, lvl: int, i: int ) -> List[Tuple[int,int]]:
        if lvl > 0:
            lvl = -1*(lvl+1)
        
        plyr   = self.queue[lvl][i]
        plyrs  = [ self.queue[lvl][i] ]
        digest = [ (lvl, i) ]
        
        # Sweep through the rest of the level we start in
        for k in range(i+1,len(self.queue[lvl])):
            if self.queue[lvl][k].areValidOpponents( plyrs ):
                plyrs.append( self.queue[lvl][k] )
                # We want to store the shifted inner index since any players in
                # front of this player will be removed
                digest.append( (lvl, k - len(digest) ) )
                if len(digest) == self.playersPerMatch:
                    # print( f'Match found: {", ".join([ p.name for p in plyrs ])}.' ) 
                    return digest
        
        # Starting from the priority level directly below the given level and
        # moving towards the lowest priority level, we sweep across each
        # remaining level looking for a match
        for l in reversed(range(-1*len(self.queue),lvl)):
            count = 0
            for k in range(len(self.queue[l])):
                if self.queue[l][k].areValidOpponents( plyrs ):
                    plyrs.append( self.queue[l][k] )
                    # We want to store the shifted inner index since any players in
                    # front of this player will be removed
                    digest.append( (l, k - count ) )
                    count += 1
                    if len(digest) == self.playersPerMatch:
                        # print( f'Match found: {", ".join([ p.name for p in plyrs ])}.' ) 
                        return digest

        # A full match couldn't be formed. Return an empty list
        return [ ]
        
    
    async def pairQueue( self ) -> None:
        # print( "Inside the pairings task" )
        time.sleep( self.pairingWaitTime )

        newQueue = []
        for _ in range(len(self.queue) + 1):
            newQueue.append( [] )
        plyrs = [ ]
        indices = [ ]
        matchTasks = [ ]

        # print( "Shuffling the queue" )
        for lvl in self.queue:
            random.shuffle( lvl )
        oldQueue = self.queue
        
        # print( "Starting while loops." )
        # print( self.queue )
        lvl = -1
        while lvl >= -1*len(self.queue):
            while len(self.queue[lvl]) > 0:
                # print( f'Inside the inner loop. There are {len(self.queue[lvl])} people in this level.' )
                indices = self.searchForOpponents( lvl, 0 )
                # If an empty array is returned, no match was found
                # Add the current player to the end of the new queue
                # and remove them from the current queue
                if len(indices) == 0:
                    # print( f'Match not found for {self.queue[lvl][0].name} whose indices where "({lvl}, 0)".' ) 
                    newQueue[lvl].append(self.queue[lvl][0])
                    del( self.queue[lvl][0] )
                else:
                    plyrs = [ ] 
                    for index in indices:
                        plyrs.append( self.queue[index[0]][index[1]].name )
                        del( self.queue[index[0]][index[1]] )
                    # print( f'Match found: {", ".join(plyrs)}.' ) 
                    matchTasks.append( self.addMatch( plyrs ) )
            lvl -= 1

        """
        print( "Current queue" )
        print( self.queue )
        print( "Current future queue" )
        print( newQueue )
        """
        
        # print( "Waiting on any match tasks to finish." )
        # Waiting for the tasks to be made
        await asyncio.gather(*matchTasks)

        # print( "Trimming future queue" )
        while len(newQueue) > 0:
            if len(newQueue[-1]) != 0:
                break
            del( newQueue[-1] )
        
        if len(newQueue) != 0:
            newQueue[0] += self.queue[0]
            self.queue = newQueue

        # print( "New Queue" )
        # print( self.queue )
        
        if len(self.queue) > self.highestPriority:
            self.highestPriority = len(self.queue)
        
        if self.queue != [ [] ] + oldQueue and sum( [ len(level) for level in self.queue ] ) > self.pairingsThreshold and not self.pairingsThread.is_alive( ):
            print( "There are still enough players to form a match. Trying again." )
            self.pairingsThread = threading.Thread( target=self.launch_pairings )
            self.pairingsThread.start( )

        # print( "Completed pairings task" )

    
    def getMatch( self, a_matchNum: int ) -> match:
        if a_matchNum > len(self.matches) + 1:
            return match( [] )
        if self.matches[a_matchNum - 1].matchNumber == a_matchNum:
            return self.matches[a_matchNum - 1]
        for mtch in self.matches:
            if mtch.matchNumber == a_matchNum:
                return mtch
    
    async def playerMatchDrop( self, a_player: str ) -> None:
        if not a_player in self.activePlayers:
            return
        await self.activePlayers[a_player].drop( )
    
    async def dropPlayer( self, a_player: str ) -> None:
        await self.playerMatchDrop( a_player )
        if a_player in self.activePlayers:
            await self.activePlayers[a_player].drop( )
            self.droppedPlayers[a_player] = self.activePlayers[a_player]
            del( self.activePlayers[a_player] )
            print( self.droppedPlayers[a_player] )
    
    async def playerCertifyResult( self, a_player: str ) -> None:
        if not a_player in self.activePlayers:
            return
        message = await self.activePlayers[a_player].certifyResult( )
        if message != "":
            await self.pairingsChannel.send( message )
    
    async def recordMatchWin( self, a_winner: str ) -> None:
        if not a_winner in self.activePlayers:
            return
        message = await self.activePlayers[a_winner].recordWin( )
        if message != "":
            await self.pairingsChannel.send( message )
    
    async def recordMatchDraw( self, a_player: str ) -> None:
        if not a_player in self.activePlayers:
            return
        message = await self.activePlayers[a_player].recordDraw( )
        if message != "":
            await self.pairingsChannel.send( message )

    def saveTournament( self, a_dirName: str ) -> None:
        if not (os.path.isdir( f'{a_dirName}' ) and os.path.exists( f'{a_dirName}' )):
           os.mkdir( f'{a_dirName}' ) 
        self.saveOverview( f'{a_dirName}/overview.xml' )
        self.saveMatches( a_dirName )
        self.savePlayers( a_dirName )
    
    def saveOverview( self, a_filename ):
        digest  = "<?xml version='1.0'?>\n"
        digest += '<tournament>\n'
        digest += f'\t<name>{self.tournName}</name>\n'
        digest += f'\t<guild id="{self.guild.id if type(self.guild) == discord.Guild else str()}">{self.hostGuildName}</guild>\n'
        digest += f'\t<role id="{self.role.id if type(self.role) == discord.Role else str()}"/>\n'
        digest += f'\t<format>{self.format}</format>\n'
        digest += f'\t<regOpen>{self.regOpen}</regOpen>\n'
        digest += f'\t<status started="{self.tournStarted}" ended="{self.tournEnded}" canceled="{self.tournCancel}"/>\n'
        digest += f'\t<deckCount>{self.deckCount}</deckCount>\n'
        digest += f'\t<queue size="{self.playersPerMatch}">\n'
        for level in range(len(self.queue)):
            for plyr in self.queue[level]:
                digest += f'\t\t<player name="{plyr.name}" priority="{level}"/>\n'
        digest += f'\t</queue>\n'
        digest += '</tournament>'
        
        with open( a_filename, 'w' ) as xmlFile:
            xmlFile.write( digest )
    
    def savePlayers( self, a_dirName: str ) -> None:
        if not (os.path.isdir( f'{a_dirName}/players/' ) and os.path.exists( f'{a_dirName}/players/' )):
           os.mkdir( f'{a_dirName}/players/' ) 

        for player in self.activePlayers:
            self.activePlayers[player].saveXML( f'{a_dirName}/players/{self.activePlayers[player].name}.xml' )
        for player in self.droppedPlayers:
            self.droppedPlayers[player].saveXML( f'{a_dirName}/players/{self.droppedPlayers[player].name}.xml' )
        

    def saveMatches( self, a_dirName: str ) -> None:
        if not (os.path.isdir( f'{a_dirName}/matches/' ) and os.path.exists( f'{a_dirName}/matches/' )):
           os.mkdir( f'{a_dirName}/matches/' ) 

        for match in self.matches:
            match.saveXML( f'{a_dirName}/matches/match_{match.matchNumber}.xml' )
        
    def loadTournament( self, a_dirName: str ) -> None:
        self.loadPlayers( f'{a_dirName}/players/' )
        self.loadOverview( f'{a_dirName}/overview.xml' )
        self.loadMatches( f'{a_dirName}/matches/' )
    
    def loadOverview( self, a_filename: str ) -> None:
        xmlTree = ET.parse( a_filename )
        tournRoot = xmlTree.getroot() 
        self.tournName = tournRoot.find( 'name' ).text
        self.guildID   = int( tournRoot.find( 'guild' ).attrib["id"] )
        self.roleID    = int( tournRoot.find( 'role' ).attrib["id"] )
        self.format    = tournRoot.find( 'format' ).text
        self.deckCount = int( tournRoot.find( 'deckCount' ).text )

        self.regOpen      = str_to_bool( tournRoot.find( 'regOpen' ).text )
        self.tournStarted = str_to_bool( tournRoot.find( 'status' ).attrib['started'] )
        self.tournEnded   = str_to_bool( tournRoot.find( 'status' ).attrib['ended'] )
        self.tournCancel  = str_to_bool( tournRoot.find( 'status' ).attrib['canceled'] )

        self.playersPerMatch = int( tournRoot.find( 'queue' ).attrib['size'] )
        
        players = tournRoot.find( 'queue' ).findall( 'player' )
        maxLevel = 1
        for plyr in players:
            if int( plyr.attrib['priority'] ) > maxLevel:
                maxLevel = int( plyr.attib['priority'] )
        for _ in range(maxLevel):
            self.queue.append( [] )
        for plyr in players:
            self.queue[int(plyr.attrib['priority'])].append( self.activePlayers[ plyr.attrib['name'] ] )
        if sum( [len(level) for level in self.queue] ) > self.playersPerMatch:
            print( "Enough players found during loading. Creating new pairing task." )
            self.pairingsThread = threading.Thread( target=self.launch_pairings )
            self.pairingsThread.start( )
    
    def loadPlayers( self, a_dirName: str ) -> None:
        playerFiles = [ f'{a_dirName}/{f}' for f in os.listdir(a_dirName) if os.path.isfile( f'{a_dirName}/{f}' ) ]
        for playerFile in playerFiles:
            newPlayer = player( "" )
            newPlayer.loadXML( playerFile )
            if newPlayer.status == "active":
                self.activePlayers[newPlayer.name]  = newPlayer
            else:
                self.droppedPlayers[newPlayer.name] = newPlayer
    
    def loadMatches( self, a_dirName: str ) -> None:
        matchFiles = [ f'{a_dirName}/{f}' for f in os.listdir(a_dirName) if os.path.isfile( f'{a_dirName}/{f}' ) ]
        for matchFile in matchFiles:
            newMatch = match( [] )
            newMatch.loadXML( matchFile )
            self.matches.append( newMatch )
            for aPlayer in newMatch.activePlayers:
                if aPlayer in self.activePlayers:
                    self.activePlayers[aPlayer].matches.append( newMatch )
                elif aPlayer in self.droppedPlayers:
                    self.droppedPlayers[aPlayer].matches.append( newMatch )
            for dPlayer in newMatch.droppedPlayers:
                if dPlayer in self.activePlayers:
                    self.activePlayers[dPlayer].matches.append( newMatch )
                elif dPlayer in self.droppedPlayers:
                    self.droppedPlayers[dPlayer].matches.append( newMatch )


